"""
Execution of generation / scoring workflows (independent of computing environment,
any backend-specific functionality must be implemented elsewhere / abstracted out)
"""
from copy import deepcopy
from importlib import import_module
from os import path, makedirs
import subprocess
import tempfile
import dill as serializer
from typing import Any
from loguru import logger
from evedesign.system import System, SystemInstance
from evedesign.sequence import read_fasta
from evedesign_server.config import Settings
from evedesign_server.schemas import (
    PipelineSpec, SystemInstanceSpec, GenerationStepSpec, ScoringStepSpec, TransformationStepSpec,
    ClassSpecBase, ModelSpecBase, ProteinToDnaSpec, ProteinToDnaResult, SingleMutationScanSpec,
    SingleMutationScanResult, SingleMutWithScore, AnalysisStepSpec, SystemSpec, ModelStatsSpec
)

SERIALIZATION_EXT = ".dill"

def instantiate_class(
    spec: ClassSpecBase | ModelSpecBase,
    system: System | None
) -> tuple[
    Any, list[tuple[ClassSpecBase | ModelSpecBase, Any]]
]:
    """
    Instantiate class based on declarative specification (or reload serialized version)

    Parameters
    ----------
    spec
        Declarative specification (specify location attribute to reload from that path)
    system
        Molecular system to be modelled with class

    Returns
    -------
    Tuple with
      i) instantiated class
      ii) list of created models (tuples of specification and instance); instantiated class i) is at end of list
    """
    # retrieve defaults for current model; we assume correctness here, if there
    # is a KeyError below this means implementation/configuration is incorrect and must be fixed
    s = Settings()

    # if location is specified on class spec, reload it
    if spec.location is not None and s.serialized_model_path is not None:
        model_path = path.join(s.serialized_model_path, f"{spec.location}{SERIALIZATION_EXT}")
        with open(model_path, "rb") as f:
            class_inst = serializer.load(f)

        if class_inst.system != system:
            raise ValueError(
                f"System of deserialized model {spec} does not agree with current system {system}"
            )

        # return loaded instance, all submodels will be included (no new models added, so return empty list)
        return class_inst, []

    # if class instance is not reloaded, create from scratch (however, nested classes might still be
    # reloaded in the following steps)
    model_defaults = s.models[spec.key]
    variant_defaults = model_defaults.variants[spec.variant]

    logger.info(
        f"Building model: {spec}\nwith\n...defaults: {model_defaults}\nand\n...variant_defaults: {variant_defaults}"
    )

    # import class
    module = import_module(model_defaults.module_name)
    class_ = getattr(module, model_defaults.class_name)

    # assemble constructor arguments to class; start with default arguments
    args = {
        **variant_defaults.args
    }

    # then add file arguments, mapped to storage configuration for backend
    if variant_defaults.file_args is not None:
        args = {
            **args,
            **{
                file_arg_name: path.join(
                    s.model_param_root, spec.key, spec.variant, file_arg_name
                )
                for file_arg_name in variant_defaults.file_args
            }
        }

    # collect any built models here so we can store for later reuse
    new_model_collection = []

    # iterate over custom arguments, build any nested classes where necessary
    if spec.args is not None:
        for arg_name, arg_value in spec.args:
            # build single nested class/model
            if isinstance(arg_value, ClassSpecBase):
                sub, sub_collection = instantiate_class(arg_value, system)
                new_model_collection.extend(sub_collection)
                args[arg_name] = sub

            # build list of nested classes/models
            elif isinstance(arg_value, list) and len(arg_value) > 0 and isinstance(arg_value[0], ClassSpecBase):
                args[arg_name] = []
                for arg_value_elem in arg_value:
                    assert isinstance(arg_value_elem, ClassSpecBase)
                    sub, sub_collection = instantiate_class(arg_value_elem, system)
                    new_model_collection.extend(sub_collection)
                    args[arg_name].append(sub)

            # simply store value to final arguments as is
            else:
                args[arg_name] = arg_value

    # apply custom transform to params
    if model_defaults.args_transform is not None:
        args = model_defaults.args_transform(args)

    logger.info(f"Final args: {args}")

    # finally, create class instance, build model if needed, and return
    class_inst = class_(**args)

    # update spec with citation string, if available
    if hasattr(class_inst,  "citations"):
        spec.citations = class_inst.citations

    if isinstance(spec, ModelSpecBase):
        class_inst.build(
            system=system, data=spec.data_transformed,
        )

        # update spec with model statistics
        stats = class_inst.stats()
        if stats is not None:
            spec.stats = ModelStatsSpec(**stats)

    # append final model instance collection (only happens for newly instantiated classes, reloaded
    # instance case already has returned at the beginning of function)
    new_model_collection.append(
        (spec, class_inst)
    )

    return class_inst, new_model_collection

def _serialize_models(new_models, run_id, serialized_model_path, step_idx):
    if new_models is not None and serialized_model_path is not None:
        for model_idx, (model_spec, model) in enumerate(new_models):
            makedirs(serialized_model_path, exist_ok=True)
            model_id = f"{run_id}-{step_idx}-{model_idx}"
            model_location = path.join(
                serialized_model_path, f"{model_id}{SERIALIZATION_EXT}"
            )
            with open(model_location, "wb") as f:
                serializer.dump(model, f)

            # update model location in place
            model_spec.location = model_id

def execute(pipeline_spec: PipelineSpec, run_id: str) -> tuple[list[SystemInstanceSpec], PipelineSpec]:
    """
    Execute design pipeline specification

    Parameters
    ----------
    pipeline_spec
        Specification of generation/scoring/transformation steps to run in a system
        (possibly starting from existing system instances)
    run_id
        Unique identifier for run

    Returns
    -------
    instances
        Instances generated/scored/transformed by running through pipeline
    pipeline_spec
        Updated version of specification with locations of saved individual models
    """
    # retrieve settings
    s = Settings()

    # make a deep copy of spec as we will mutate it in place to track saved models
    pipeline_spec = deepcopy(pipeline_spec)

    # deserialize system; use our own deserialization code here since more complex
    # types may be underneath (e.g. 3D structures)
    system = System.deserialize(pipeline_spec.system.model_dump())

    # initialize system instances (leave verification to individual steps; might add extra verification
    # to pipeline spec schema validation)
    if pipeline_spec.system_instances is not None:
        system_instances = [
            SystemInstance.deserialize(
                si.model_dump()
            ) for si in pipeline_spec.system_instances
        ]
    else:
        system_instances = None

    for i, step in enumerate(pipeline_spec.steps):
        logger.info(f"Executing step {i + 1}... {step}\n")
        new_models = None

        if isinstance(step, GenerationStepSpec):
            # instantiate generator
            generator, new_models = instantiate_class(
                step.generator, system
            )

            # generate new designs
            system_instances = generator.generate(
                **step.args.model_dump()
            )

        elif isinstance(step, ScoringStepSpec):
            # instantiate scorer
            scorer, new_models = instantiate_class(
                step.scorer, system
            )

            # score instances
            scored_instances = scorer.score(system_instances)

            # check if we keep the score as main score, or route to metadata
            # on existing instances
            if step.metadata_key is None:
                system_instances = scored_instances
            else:
                for j, (instance, scored_instance) in enumerate(
                    zip(system_instances, scored_instances)
                ):
                    if instance.metadata is None:
                        instance.metadata = {}
                    instance.metadata[step.metadata_key] = scored_instance.score

        elif isinstance(step, TransformationStepSpec):
            # instantiate transformer
            transformer, new_models = instantiate_class(
                step.transformer, system
            )

            # transform instances
            system_instances = transformer.transform(system_instances)
        elif isinstance(step, AnalysisStepSpec):
            # instantiate analyzer
            analyzer, new_models = instantiate_class(
                step.analyzer, system
            )

            # run analysis
            system, system_instances = analyzer.analyze(
                system,
                system_instances,
                step.analyzer.model_dump().get("data"),
                step.entity
            )

            # update system in pipeline specification (need to transform to pydantic)
            pipeline_spec.system = SystemSpec(**system.serialize())

        # serialize newly created models after each step
        _serialize_models(
            new_models, run_id, s.serialized_model_path, i
        )

    # serialize system instances with custom serializer, then turn into pydantic model for easier integration
    # with API endpoints but could remove as this is very redundant;
    instances = [
        SystemInstanceSpec(**si.serialize()) for si in system_instances
    ]

    # return serialized system instances and updated pipeline spec with stored model locations
    return instances, pipeline_spec

def single_mutation_scan(
    single_mutation_scan_spec: SingleMutationScanSpec,
    run_id: str
) -> tuple[
    list[SingleMutationScanResult], SingleMutationScanSpec
]:
    """
    Execute single mutation matrix scan computation

    Parameters
    ----------
    single_mutation_scan_spec
        Specification to execute
    run_id
        Unique identifier for run

    Returns
    -------
    single_scores
        Scores for all requested single mutations
    single_mutation_scan_spec
        Updated version of specification with locations of saved model(s)
    """
    # retrieve settings
    s = Settings()

    # make a deep copy of spec as we will mutate it in place to track saved models
    single_mutation_scan_spec = deepcopy(single_mutation_scan_spec)

    # deserialize system; use our own deserialization code here since more complex
    # types may be underneath (e.g. 3D structures)
    system = System.deserialize(single_mutation_scan_spec.system.model_dump())

    # deserialize target instance
    system_instance = SystemInstance.deserialize(
        single_mutation_scan_spec.system_instance.model_dump()
    )

    # instantiate scorer
    scorer, new_models = instantiate_class(
        single_mutation_scan_spec.scorer, system
    )

    # score single mutations
    scores = scorer.single_mutation_scan(
        instance=system_instance,
        entity=single_mutation_scan_spec.entity,
        positions=single_mutation_scan_spec.positions
    )

    # serialize newly created models after each step
    _serialize_models(
        new_models, run_id, s.serialized_model_path, step_idx=0
    )

    # drop any null values (e.g. due to mixed alphabets across multiple entities)
    single_scores = [
        SingleMutationScanResult(
            entity=entity,
            pos=pos,
            ref=ref,
            subs=[
                SingleMutWithScore(
                    to=to,
                    score=score
                ) for (to, score) in row.dropna().items()
            ]
        )
        for (entity, pos, ref), row in scores.iterrows()
]

    # return serialized mutant scores and updated pipeline spec with stored model locations
    return single_scores, single_mutation_scan_spec

def protein_to_dna(protein_to_dna_spec: ProteinToDnaSpec) -> list[ProteinToDnaResult]:
    """
    Execute protein-to-dna specification (e.g. codon optimization)

    Note that output will retain potential duplicates in input system instances; deduplication
    is responsibility of caller as right approach will depend on context

    Parameters
    ----------
    protein_to_dna_spec
        Specification to execute

    Returns
    -------
    List of optimization results (each DNA sequence corresponds to one list element).
    """
    # instantiate codon optimizer
    opt, _ = instantiate_class(
        protein_to_dna_spec.optimizer, system=None
    )

    # prepare arguments for optimize call

    # system to optimize in
    system = System.deserialize(protein_to_dna_spec.args.system.model_dump())

    # designs to turn into DNA sequences
    system_instances = [
       SystemInstance.deserialize(
           si.model_dump()
       ) for si in protein_to_dna_spec.args.system_instances
    ]

    # optional reference instance to optimize sequences relative to
    if protein_to_dna_spec.args.reference is not None:
        ref_instance = SystemInstance.deserialize(
           protein_to_dna_spec.args.reference.model_dump()
       )
    else:
        ref_instance = None

    # run optimization
    dna_seqs = opt.optimize(  # noqa
        system=system,
        instances=system_instances,
        entity=protein_to_dna_spec.args.entity,
        upstream_dna=protein_to_dna_spec.args.upstream_dna,
        downstream_dna=protein_to_dna_spec.args.downstream_dna,
        reference=ref_instance,
        reference_dna=protein_to_dna_spec.args.reference_dna,
    )

    # turn into list of pydantic models and return
    return [
        ProteinToDnaResult(**row) for row in dna_seqs.to_dict(orient="records")
    ]


def filter_a3m(
    spec: PipelineSpec | SingleMutationScanSpec,
    hhfilter_id_cutoff: int = 90,
    hhfilter_path: str = "hhfilter"
) -> PipelineSpec | SingleMutationScanSpec:
    """
    Quick and dirty helper function to filter MSAs associated with entities
    (these can have substantial duplicated entries due to merging of UniRef and env sequences
    as returned by MMSeqs server)
    """
    spec = deepcopy(spec)

    # iterate through entities and update in place
    for i, entity in enumerate(spec.system.entities):
        if entity.type != "protein" or entity.sequences is None:
            continue

        with tempfile.TemporaryDirectory() as tempdir:
            msa_source = path.join(tempdir, "msa_source")
            msa_target = path.join(tempdir, "msa_target")

            # write raw MSA
            source_seqs = {}
            with open(msa_source, "w") as outfile:
                for seq in entity.sequences.seqs:
                    source_seqs[seq.id] = seq
                    outfile.write(f">{seq.id}\n")
                    outfile.write(f"{seq.seq}\n")

            try:
                result = subprocess.run(
                    [
                        hhfilter_path,
                        "-i", msa_source,
                        "-o", msa_target,
                        "-id", str(hhfilter_id_cutoff)
                    ],
                    capture_output=True,
                    shell=False
                )
            except FileNotFoundError as e:
                raise RuntimeError(
                    f"hhfilter not found at '{hhfilter_path}'. It is required to "
                    f"filter the alignment of entity '{entity.id}'; install hhsuite "
                    f"or pass hhfilter_path."
                ) from e

            if result.returncode != 0:
                raise RuntimeError(
                    f"hhfilter failed with exit code {result.returncode} while "
                    f"filtering the alignment of entity '{entity.id}'.\n"
                    f"stderr: {result.stderr.decode(errors='replace').strip()}"
                )

            # rebuild sequence list from remaining sequences
            with open(msa_target) as infile:
                entity.sequences.seqs = [
                    source_seqs[id_] for id_, _ in read_fasta(infile)
                ]

    return spec