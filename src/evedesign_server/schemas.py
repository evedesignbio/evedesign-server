"""
API schemas for instantiating design module classes
"""
from os import path
from typing import Any, Annotated, Literal, Self, Optional, Callable

from pydantic import BaseModel, Field, PositiveInt, PositiveFloat, model_validator, TypeAdapter
from evedesign_server.constants import RESTRICTION_SITES, HLA_CLASS2_ALLELES_TYPE

# duplication of type from main package
BioPolymer = Literal["protein", "dna", "rna"]

# duplication of type from main package
EvaluationScoreName = Literal[
    "r2", "pearson", "spearman", "rocauc", "mcc", "average_precision"
]

# currently only protein sequences or mask character
VALID_REP_PATTERN = r"^[ACDEFGHIKLMNPQRSTVWY\*]+$"
VALID_PROTEIN_REP_WITH_DELETION_PATTERN = r"^[ACDEFGHIKLMNPQRSTVWY\-]+$"
VALID_ALIGNED_SEQ_PATTERN = r"^[a-zA-Z\-\.]+$"
VALID_DNA_PATTERN = r"^[atgcATGC]*$"


class SequenceSpec(BaseModel):
    """
    Single sequence specification, directly maps to Sequence class from main package
    """
    seq: str = Field(min_length=1, pattern=VALID_ALIGNED_SEQ_PATTERN)
    id: str | None = None
    key: str | None = None
    type: BioPolymer = Field("protein")
    metadata: dict[str, Any] | None = None


class SequencesSpec(BaseModel):
    """
    Sequence collection specification, directly maps to Sequences class from main package
    """
    seqs: list[SequenceSpec]
    aligned: bool = False
    type: Literal["protein", "dna", "rna"] = Field("protein")
    weights: list[float] | None = None
    format: Literal["a3m", "a2m", "fasta"] | None = None


class EntitySpec(BaseModel):
    """
    Restricted set of attributes to wrap Entity class

    TODO: right now only allows to instantiate protein entities and
     requires defined sequence representation; relax requirements as more methods
     are added to API

    TODO: increase required minimum length to scientifically meaningful limit

    TODO: add structure models field and other extended attributes when needed
    """
    type: Literal["protein"]
    rep: str = Field(min_length=1, pattern=VALID_REP_PATTERN)
    id: str
    first_index: PositiveInt
    sequences: SequencesSpec | None = None
    structures: dict[str, list[str]] | None = None
    deletions: bool | None = False
    copies: int | None = None


class SystemSpec(BaseModel):
    """
    Molecular system, maps to main package class evedesign.system.System
    """
    schema_version: Literal["0.2"]

    entities: Annotated[
        list[EntitySpec], Field(min_length=1)
    ]


class EntityInstanceSpec(BaseModel):
    """
    Restricted set of attributes to wrap EntityInstance class

    TODO: increase required minimum length to scientifically meaningful limit
    TODO: add embeddings
    """
    rep: str = Field(min_length=1, pattern=VALID_PROTEIN_REP_WITH_DELETION_PATTERN)
    # serialized CIF models
    models: dict[str, list[str]] | None


class SystemInstanceSpec(BaseModel):
    """
    System instance representation
    """
    entity_instances: list[EntityInstanceSpec]
    score: float | None = None
    confidence: float | None = None
    metadata: dict[str, Any] | None = None
    id: str | None = None
    schema_version: Literal["0.2"]


class ClassSpecBase(BaseModel):
    """
    Base class for dynamically instantiable classes for design
    (samplers, or subclassed to biomolecular models)
    """
    # attributes linking model name to implementation (will be used to
    # instantiate discriminated union)
    key: str

    # specific model subtype (e.g. model size)
    variant: str

    # arguments passed to constructor
    args: Any

    # unique ID of class/model instance (will be used to serialize model)
    id: str | None = None

    # if specified, reload class based on this identifier (must be present in model repository,
    # note we have two identifiers so a loaded model does not get overwritten if any changes are
    # applied to it)
    source_id: str | None = None

    @model_validator(mode="after")
    def _validate_spec(self) -> Self:
        # verify so user cannot inject a relative or absolute path
        if self.source_id is not None and path.split(self.source_id)[-1] != self.source_id:
            raise ValueError(
                "Invalid from_id specification"
            )

        # verify so user cannot inject a relative or absolute path
        if self.id is not None and path.split(self.id)[-1] != self.id:
            raise ValueError(
                "Invalid id specification"
            )

        return self

    # list of citations for class (will be attached during execution)
    citations: list[str] | None = None


class ModelStatsSpec(BaseModel):
    """
    Model statistics representation
    """
    y_true: list[list[float]] | None
    y_pred: list[list[float]] | None
    scores: dict[EvaluationScoreName, list[float]] | None


class ModelSpecBase(ClassSpecBase):
    """
    Base class for attributes to instantiate design models
    """
    # data to pass to build() method call
    data: Any

    # transformation of data for injection into framework code;
    # unless overwritten return data as-is;
    # we on purpose do not use self.data.transform as this may not be
    # present on some types (None, list); this also gives greater flexibility
    # to modify inputs as needed in different settings
    @property
    def data_transformed(self):
        return self.data

    # summary statistics for model (e.g. correlation on fitting experimental data)
    stats: ModelStatsSpec | None = None

    # if True, call build() again after loading (only has an effect if reuse=True);
    # allows to e.g. finetune existing models
    rebuild_on_load: bool = Field(default=False)


class DatasetSplitSpec(BaseModel):
    train: list[int]
    test: list[int] | None = None
    val: list[int] | None = None


class LabeledInstanceDatasetSpec(BaseModel):
    """
    Pydantic equivalent of evedesign.dataset.LabeledInstanceDataset
    """
    instances: list[SystemInstanceSpec]

    # mapping from label name to labels, must have same length as instances
    labels: dict[str, list[float | None]]

    # optional dataset splits
    splits: dict[str, DatasetSplitSpec] | tuple[Literal["cv"], int] | None = None

    # final train/validation split
    final_train_val_split: DatasetSplitSpec | None = None

    # duplication of logic in LabeledInstanceDataset class
    @model_validator(mode="after")
    def _validate_spec(self) -> Self:
        if len(self.labels) == 0:
            raise ValueError(
                "Must specify at least one series in labels dictionary"
            )

        for name, series in self.labels.items():
            if len(self.instances) != len(series):
                raise ValueError(
                    f"Length of instances and values for series {name} does not agree for training set"
                )

        return self

    @property
    def model_transformed(self):
        # only import upon transformation in pipeline to avoid potentially costly imports in API
        from evedesign.system import SystemInstance
        from evedesign.dataset import LabeledInstanceDataset

        if isinstance(self.splits, dict):
            # exclude_unset to remove keys with None (e.g. validation set not specified)
            splits = self.model_dump(exclude_unset=True)["splits"]
        else:
            splits = self.splits

        if self.final_train_val_split is not None:
            final_train_val_split = self.final_train_val_split.model_dump()
        else:
            final_train_val_split = None

        return LabeledInstanceDataset(
            instances=[
                SystemInstance.deserialize(inst.model_dump()) for inst in self.instances
            ],
            labels=self.labels,
            splits=splits,
            final_train_val_split=final_train_val_split
        )


class SupervisedModelSpecBase(ModelSpecBase):
    """
    Base class for supervised models that receive labeled instances as data
    """
    data: LabeledInstanceDatasetSpec

    @property
    def data_transformed(self):
        return self.data.model_transformed


class AnalysisSpecBase(ClassSpecBase):
    """
    Base class for attributes to instantiate analysis classes
    (not using a build method)
    """
    # data to pass to analyze() method call
    data: Any


class TemperatureSchedule(BaseModel):
    type: Literal["linear"]
    update: float

InitStrategy = Literal["random", "system"]
ScanOrder = Literal["random", "sequential"]


class EVmutation2ArgsSpec(BaseModel):
    """
    Parameters of EVmutation2 that can be modified based on job specification;
    will override any defaults specified in config.py
    """
    encoder_num_samples: int = Field(4, ge=1, le=8)
    encoder_num_recycling_steps: int = Field(4, ge=1, le=4)
    encoder_max_num_msa: int = Field(1024, ge=128, le=2048)
    decoder_num_full_samples: int = Field(16, ge=1, le=16)
    decoder_num_mutant_samples: int = Field(1, ge=1, le=16)
    decoder_batch_size: int | Literal["auto"] = Field(64, ge=1, le=1024)


class EVmutation2Spec(ModelSpecBase):
    # note: must match keys specified in config.py
    key: Literal["evmutation2"]
    variant: Literal["msa-only-small"] = Field("msa-only-small")

    # user-exposed model parameters with strict verification
    args: EVmutation2ArgsSpec = Field(
        default_factory=EVmutation2ArgsSpec
    )

    # model does not take any additional data as input, so fix to None
    data: None = None


class ESM2ArgsSpec(BaseModel):
    """
    Parameters of ESM2 that can be modified based on job specification;
    will override any defaults specified in config.py
    """
    batch_size: int | Literal["auto"] = Field(512, ge=1, le=1024)

    # Gibbs sampler arguments for generate()
    # num_sweeps: int = Field(500, ge=1, le=1000)
    # init_strategy: InitStrategy = Field("system")
    # scan_order: ScanOrder = Field("random")
    # temperature_schedule: TemperatureSchedule | None = None


class ESM2Spec(ModelSpecBase):
    # note: must match keys specified in config.py
    key: Literal["esm2"]
    variant: Literal["esm2_t33_650M_UR50D"] = Field("esm2_t33_650M_UR50D")

    # user-exposed model parameters with strict verification
    args: ESM2ArgsSpec = Field(
        default_factory=ESM2ArgsSpec
    )

    # model does not take any additional data as input, so fix to None
    data: None = None


class Boltz2ArgsSpec(BaseModel):
    """
    Parameters of Boltz2 that can be modified based on job specification;
    will override any defaults specified in config.py
    """
    batch_size: int | Literal["auto"] = Field(16, ge=1, le=1024)
    sampling_steps: int = Field(200, ge=1, le=1000)
    diffusion_samples: int = Field(1, ge=1, le=16)
    recycling_steps: int = Field(3, ge=1, le=8)
    use_msa: bool = Field(True)

    score_attribute: Literal[
        "iptm", "ptm", "confidence_score", "complex_plddt"
    ] = Field("confidence_score")

    confidence_attribute: Literal[
        "iptm", "ptm", "confidence_score", "complex_plddt"
    ] = Field("complex_plddt")


class Boltz2Spec(ModelSpecBase):
    # note: must match keys specified in config.py
    key: Literal["boltz2"]
    variant: Literal["default"] = Field("default")

    # user-exposed model parameters with strict verification
    args: Boltz2ArgsSpec = Field(
        default_factory=Boltz2ArgsSpec
    )

    # model does not take any additional data as input, so fix to None
    data: None = None


class MixMHC2PredArgsSpec(BaseModel):
    """
    Arguments for MixMHC2pred allele scorer
    """
    alleles: dict[
        HLA_CLASS2_ALLELES_TYPE, Annotated[float, Field(ge=0)]   # noqa
    ] = Field(min_length=1)

    peptide_lengths: list[
        Annotated[int, Field(ge=9, le=21)]
    ] = Field(
        default=[15], min_length=1, max_length=13
    )

    floor_rank: float = Field(
        default=1e-05, ge=0, le=1
    )

    truncate_rank: float | None = Field(
        default=None, ge=0, le=100
    )


class MixMHC2PredSpec(ModelSpecBase):
    key: Literal["mixmhc2pred"]
    variant: Literal["default"]
    args: MixMHC2PredArgsSpec = Field(
        default_factory=MixMHC2PredArgsSpec
    )
    data: None = None


class SeqDistRestraintTargetSpec(BaseModel):
    """
    Single target sequence for entity sequence restraints
    """
    entity: int = Field(ge=0)
    rep: str = Field(
        min_length=1, pattern=VALID_PROTEIN_REP_WITH_DELETION_PATTERN
    )


class LinearSeqDistRestraintSpec(ModelSpecBase):
    key: Literal["linear_seq_dist_restraint"]
    variant: Literal["default"]
    args: None = None
    data: list[SeqDistRestraintTargetSpec] = Field(min_length=1)

    @property
    def data_transformed(self):
        data_t = {}
        for entity_seq_spec in self.data:
            if entity_seq_spec.entity not in data_t:
                data_t[entity_seq_spec.entity] = []

            data_t[entity_seq_spec.entity].append(entity_seq_spec.rep)

        return data_t


class MotifSpec(BaseModel):
    name: str
    regex: str = Field(min_length=1) # TODO: validate valid regex

    # no constraint on offset values to allow maximum flexibility, values
    # are extracted using dict.get in corresponding class
    exposure_offsets: list[int]


class ExposedMotifRestraintArgsSpec(BaseModel):
    """
    Arguments for exposed sequence motif restraint
    """
    # motifs: list[MotifSpec] = Field(min_length=1, max_length=64)  # TODO: revert to validated form, cannot ingest directly
    motifs: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    exposure_cutoff: float = Field(default=0.2, ge=0, le=1)
    exposure_source: Literal["system", "instance"] | None = Field(default="system")
    exposure_agg: Literal["mean", "median", "min", "max"] = Field(default="max")
    missing_exposure: Literal["exclude", "include"] = Field(default="include")


class ExposedMotifRestraintSpec(ModelSpecBase):
    key: Literal["exposed_motif_restraint"]
    variant: Literal["default"]
    args: ExposedMotifRestraintArgsSpec = Field(
        default_factory=ExposedMotifRestraintArgsSpec
    )
    data: None = None


class SklearnPredictorArgsSpec(BaseModel):
    """
    Constructor arguments for evedesign.models.supervised.SklearnPredictorOnEmbeddingsScores
    """
    # can extend with more regressors/classifiers here
    predictor: Literal[
        "RandomForestRegressor",
        "RidgeCV",
        "LogisticRegressionCV",
        "LinearRegression",
        "LassoCV",
        "ElasticNetCV",
    ] = Field("RandomForestRegressor")
    predictor_kwargs: dict[str, Any] | None = None
    embedder: "Optional[TransformerSpec]" = None
    scorer: "Optional[ScorerSpec]" = None
    use_embeddings: bool = True
    use_scores: bool = True
    override_models_for_training: bool = False
    target_name: str | None = None
    pooling: Literal["mean", "max"] | None = Field("mean")
    batch_size: int | None = Field(128, ge=1, le=1024)


class SklearnPredictorSpec(SupervisedModelSpecBase):
    """
    Supervised sklearn predictor on top of embeddings/scores
    """
    key: Literal["supervised_sklearn_predictor"]
    variant: Literal["default"]

    args: SklearnPredictorArgsSpec = Field(
        default_factory=SklearnPredictorArgsSpec
    )

# discriminated union - list all classes implementing "Scorer" interface here
ScorerSpec = Annotated[
    EVmutation2Spec | ESM2Spec | SklearnPredictorSpec | MixMHC2PredSpec |
    ExposedMotifRestraintSpec | LinearSeqDistRestraintSpec,
    Field(discriminator="key")
]

# discriminated union - list all classes implementing "ConditionalMutationScorer" interface here
ConditionalMutationScorerSpec = Annotated[
    EVmutation2Spec | ESM2Spec | SklearnPredictorSpec | MixMHC2PredSpec |
    ExposedMotifRestraintSpec | LinearSeqDistRestraintSpec,
    Field(discriminator="key")
]

# discriminated union - list all classes implementing "Transformer" interface here
TransformerSpec = Annotated[
    EVmutation2Spec | ESM2Spec | Boltz2Spec,
    Field(discriminator="key")
]


class GibbsSamplerArgsSpec(BaseModel):
    scorers: Annotated[
        list[ConditionalMutationScorerSpec], Field(min_length=1)
    ]
    weights: list[float] | None
    num_sweeps: int = Field(500, ge=1, le=1000)
    init_strategy: InitStrategy
    scan_order: ScanOrder
    # sampler expects lambda function, need to map in between from declarative specification here
    temperature_schedule: TemperatureSchedule | None
    record_full_chain: bool = False

    @model_validator(mode="after")
    def _validate_spec(self) -> Self:
        if self.weights is not None and len(self.scorers) != len(self.scorers):
            raise ValueError("Length of scorers and weights must agree")

        return self


class GibbsSamplerSpec(ClassSpecBase):
    """
    Gibbs sampler specification
    """
    key: Literal["gibbs"]
    variant: Literal["default"]

    args: GibbsSamplerArgsSpec = Field(
        default_factory=GibbsSamplerArgsSpec
    )


# discriminated union - list all classes implementing "Generator" interface here
GeneratorSpec = Annotated[
    EVmutation2Spec | GibbsSamplerSpec,
    Field(discriminator="key")
]


class GenerateArgsSpec(BaseModel):
    """
    Parameters for call to generate() method of Generator implementations
    """
    num_designs: PositiveInt
    entities: Annotated[list[int], Field(min_length=1)] | None
    fixed_pos: dict[int, list[int]] | None
    temperature: PositiveFloat


class GenerationStepSpec(BaseModel):
    """
    Generate step in pipeline
    """
    key: Literal["generate"]

    generator: GeneratorSpec

    # generate() method call parameters
    args: GenerateArgsSpec


class ScoringStepSpec(BaseModel):
    """
    Arbitrary instance scoring step
    """
    key: Literal["score"]

    # model to apply to instances for scoring
    scorer: ScorerSpec

    # if None, will overwrite main score attribute of system instance,
    # otherwise, score will be routed to metadata
    metadata_key: str | None = None


class TransformationStepSpec(BaseModel):
    """
    Arbitrary instance transformation step
    """
    key: Literal["transform"]

    # model to apply to instances for scoring
    transformer: TransformerSpec

    # TODO: eventually add args/entity to full specify transform() signature... for now only single entity


class SequenceSpaceMDSArgsSpec(BaseModel):
    num_components: int = 2
    include_system_sequences: bool = True
    system_sequence_fragment_filter: float | None = 0.7
    # expose mds_kwargs?


class SequenceSpaceUMAPArgsSpec(BaseModel):
    num_components: int = 2
    include_system_sequences: bool = True
    system_sequence_fragment_filter: float | None = 0.7
    # expose umap_kwargs?

class SequenceSpaceLandmarkMDSArgsSpec(BaseModel):
    num_components: int = 2
    include_system_sequences: bool = True
    system_sequence_fragment_filter: float | None = 0.7
    num_landmarks: int = 1000
    landmark_source: Literal["all", "system", "instances"] = Field("all")
    landmark_selection_mode: Literal["random", "mmseqs"] = Field("mmseqs")
    distance_matrix_mode: Literal["aligned", "mmseqs"] = Field("aligned")
    # expose mds_kwargs?

class SequenceSpaceMDSSpec(AnalysisSpecBase):
    # note: must match keys specified in config.py
    key: Literal["seqspace_mds_aligned"]
    variant: Literal["default"] = Field("default")

    args: SequenceSpaceMDSArgsSpec = Field(
        default_factory=SequenceSpaceMDSArgsSpec
    )
    data: None = None


class SequenceSpaceUMAPSpec(AnalysisSpecBase):
    # note: must match keys specified in config.py
    key: Literal["seqspace_umap_aligned"]
    variant: Literal["default"] = Field("default")

    args: SequenceSpaceUMAPArgsSpec = Field(
        default_factory=SequenceSpaceUMAPArgsSpec
    )
    data: None = None


class SequenceSpaceLandmarkMDSSpec(AnalysisSpecBase):
    # note: must match keys specified in config.py
    key: Literal["seqspace_landmarkmds"]
    variant: Literal["default"] = Field("default")

    args: SequenceSpaceLandmarkMDSArgsSpec = Field(
        default_factory=SequenceSpaceLandmarkMDSArgsSpec
    )
    data: None = None


# discriminated union - list all classes implementing "Transformer" interface here
AnalyzerSpec = Annotated[
    SequenceSpaceMDSSpec | SequenceSpaceUMAPSpec | SequenceSpaceLandmarkMDSSpec,
    Field(discriminator="key")
]


class AnalysisStepSpec(BaseModel):
    """
    Arbitrary instance transformation step
    """
    key: Literal["analyze"]

    # model to apply to instances for analysis
    analyzer: AnalyzerSpec

    entity: int | None


class PipelineSpec(BaseModel):
    """
    Computing pipeline comprised of one or more serial steps
    """
    key: Literal["pipeline"]
    schema_version: Literal["0.2"]

    # System that will be designed in pipeline
    system: SystemSpec

    # Starting instances (for scoring-only pipelines, e.g. to rescore designs
    system_instances: Annotated[
        list[SystemInstanceSpec], Field(min_length=1)
    ] | None = None

    # Steps of computational pipeline to apply
    steps: Annotated[
        list[
            Annotated[
                GenerationStepSpec | ScoringStepSpec | TransformationStepSpec | AnalysisStepSpec,
                Field(discriminator="key")
            ],
        ], Field(min_length=1)
    ]

    # optional annotation (e.g. for frontend to store where alignment was sourced from)
    metadata: dict[str, Any] | None

    @model_validator(mode="after")
    def _validate_spec(self) -> Self:
        # verification if starting with generation
        if isinstance(self.steps[0], GenerationStepSpec):
            if self.system_instances is not None:
                raise ValueError(
                    "system_instances must be None if first step is GenerationStepSpec"
                )

            # verify if entity/position specification in step are valid (needs system information
            # specified in this schema, so perform validation here)
            args = self.steps[0].args
            entity_to_pos = {
                entity_idx: list(range(entity.first_index, entity.first_index + len(entity.rep)))
                for entity_idx, entity in enumerate(self.system.entities)
                if entity.type == "protein"
            }

            if args.entities is not None:
                invalid_entities = [
                    entity_idx for entity_idx in args.entities if entity_idx not in entity_to_pos
                ]

                if len(invalid_entities) > 0:
                    raise ValueError(
                        f"Invalid entities specified in generate step: {invalid_entities}"
                    )

            if args.fixed_pos is not None:
                for entity_idx, pos_selection in args.fixed_pos.items():
                    if entity_idx not in entity_to_pos:
                        raise ValueError(
                            f"Invalid entity in fixed_pos: {entity_idx}"
                        )

                    invalid_pos = [
                        pos for pos in pos_selection if pos not in entity_to_pos[entity_idx]
                    ]
                    if len(invalid_pos) > 0:
                        raise ValueError(
                            f"Invalid positions selected for entity {entity_idx}: {invalid_pos}"
                        )

        else:  # verification if starting with scoring/transformation/...
            if self.system_instances is None:
                raise ValueError(
                    "First step must be GenerationStepSpec or system_instances must be specified"
                )

        for step in self.steps[1:]:
            if isinstance(step, GenerationStepSpec):
                raise ValueError(
                    "Only first step in pipeline can be a GenerationStepSpec"
                )

        return self


class SingleMutationScanSpec(BaseModel):
    """
    Computing pipeline comprised of one or more serial steps
    """
    key: Literal["single_mutation_scan"]
    schema_version: Literal["0.2"]

    # System that will be designed in pipeline
    system: SystemSpec

    # Starting instances (for scoring-only pipelines, e.g. to rescore designs
    system_instance: SystemInstanceSpec

    # model to apply to instances for scoring
    scorer: ScorerSpec

    # entity to restrict scoring to (score all if None)
    entity: int | None

    # positions to limit scoring to (score all if None;
    # note this setting is ambiguous if there is more than one entity)
    positions: list[int] | None

    # optional annotation (e.g. for frontend to store where alignment was sourced from)
    metadata: dict[str, Any] | None

    @model_validator(mode="after")
    def _validate_spec(self) -> Self:
        if self.positions is not None and self.entity is None:
            raise ValueError(
                "The entity field must be specified if using positions field"
            )

        return self


class SingleMutWithScore(BaseModel):
    """
    Single substitution score
    """
    to: str
    score: float


class SingleMutationScanResult(BaseModel):
    """
    Result of single mutation scan (for one position in an entity with symbol ref)
    """
    entity: int
    pos: int
    ref: str
    subs: list[SingleMutWithScore]


class ProteinToDnaOptimizerArgsSpec(BaseModel):
    """
    Arguments to ProteinToDnaOptimizer optimize() call
    """
    ...
    system: SystemSpec

    system_instances: Annotated[
        list[SystemInstanceSpec], Field(min_length=1)
    ]

    entity: int
    upstream_dna: str = Field(pattern=VALID_DNA_PATTERN)
    downstream_dna: str = Field(pattern=VALID_DNA_PATTERN)
    reference: SystemInstanceSpec | None = None
    reference_dna: Annotated[
        str, Field(pattern=VALID_DNA_PATTERN)
    ] | None = None


class DnaChiselArgsSpec(BaseModel):
    """
    Constructor arguments to instantiate
    """
    method: Literal["use_best_codon", "match_codon_usage"]
    codon_usage_table: Literal[
        "b_subtilis",
        "d_melanogaster",
        "m_musculus_domesticus",
        "m_musculus",
        "e_coli",
        "g_gallus",
        "c_elegans",
        "s_cerevisiae",
        "h_sapiens",
    ] | str
    avoid_sites: list[Literal[tuple(RESTRICTION_SITES)]]
    gc_min: float | None = Field(0.4)
    gc_max: float | None = Field(0.6)
    gc_window: int | None = Field(30)
    max_homopolymer_length: int | None = Field(5)
    max_repeat_length: int | None = Field(9)
    avoid_hairpins: bool = Field(True)
    genetic_code: Literal["Standard"] = Field("Standard")


class DnaChiselSpec(ClassSpecBase):
    key: Literal["dnachisel"]
    variant: Literal["default"] = Field("default")
    args: DnaChiselArgsSpec


class ProteinToDnaSpec(BaseModel):
    """
    Execution specification for protein to dna sequence optimization runs
    """
    key: Literal["protein_to_dna"]
    schema_version: Literal["0.2"]

    # discriminated union to select codon optimizer
    optimizer: Annotated[
        DnaChiselSpec,
        Field(discriminator="key")
    ]

    # optimize() method call arguments
    args: ProteinToDnaOptimizerArgsSpec


class ProteinToDnaResult(BaseModel):
    """
    Single-sequence result of protein to dna optimization run
    (full result will be a list)
    """
    rep: str
    dna: str
    score: float | None

InputSpec = Annotated[
    PipelineSpec | SingleMutationScanSpec | ProteinToDnaSpec,
    Field(discriminator="key")
]

InputSpecAdapter = TypeAdapter(InputSpec)

InputSpecTypeKeys = Literal["pipeline", "single_mutation_scan", "protein_to_dna"]

def condense_spec(spec: InputSpec):
    """
    Create a reduced version of spec without natural sequences and large metadata (e.g. FoldSeek hits).
    Method will work on a deep copy of original spec.

    Parameters
    ----------
    spec
        Input specification that should be condensed to more minimal version

    Returns
    -------
    Condensed copy of spec
    """
    spec_copy = spec.model_copy(deep=True)

    if isinstance(spec_copy, PipelineSpec) or isinstance(spec_copy, SingleMutationScanSpec):
        for entity in spec_copy.system.entities:
            entity.sequences = None

        if spec_copy.metadata is not None and "structure_search_result" in spec_copy.metadata:
            del spec_copy.metadata["structure_search_result"]

    if isinstance(spec_copy, ProteinToDnaSpec):
        for entity in spec_copy.args.system.entities:
            entity.sequences = None
        spec_copy.args.system_instances = []

    return spec_copy


class ModelVariantDefinition(BaseModel):
    # mapping of constructor param file argument names to
    # external URL where these files can be retrieved
    file_args: dict[str, str] | dict[str, list[tuple[str, str]]] | None

    # default parameters to supply to model
    args: dict[str, Any]


class ModelDefinition(BaseModel):
    # used to conditionally import models only when used
    # using importlib
    module_name: str
    class_name: str

    # defines whether to use GPU/CPU compute
    gpu_required: bool

    # different model variants of same base model
    # (e.g. different model sizes); each can have its own
    # set of default parameters passed to the constructor
    variants: dict[str, ModelVariantDefinition]

    # custom parameter applied to parameters before applying as model args
    args_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None
