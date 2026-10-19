from typing import Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from evedesign_server.schemas import TemperatureSchedule, InputSpecTypeKeys, ModelDefinition

ENV_PREFIX = "designserver_"

EVMUTATION2_DEFAULTS = dict(
    encoder_num_samples=4,
    encoder_num_recycling_steps=4,
    encoder_max_num_msa=1024,
    decoder_batch_size=512,
    decoder_num_full_samples=16,
    decoder_num_mutant_samples=1,
    keep_model_after_build=False,  # do not share params to avoid pickled version to contain all params
    device="cuda",
)

ESM2_DEFAULTS = dict(
    device="cuda"
)


def transform_gibbs_args(args: dict[str, Any]) -> dict[str, Any]:
    # make shallow copy
    args = {
        **args
    }
    ts: TemperatureSchedule | None = args["temperature_schedule"]  # noqa
    if ts is not None:
        if ts.type == "linear":
            args["temperature_schedule"] = (
                lambda init_temp, cur_sweep, total_sweeps, cur_step, total_steps: init_temp - cur_sweep * ts.update
            )

    return args


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX
    )

    # base directory for storing model parameters;
    # directory convention is {model_root}/{model_name}/{param_file_key}
    model_param_root: str = "/models"

    serialized_model_path: str | None = None

    # job database access settings
    db_connection_string: str | None = None

    @property
    def db_connection_string_sync(self) -> str | None:
        if self.db_connection_string is not None:
            return self.db_connection_string.replace(
                "postgresql://", "postgresql+psycopg://"
            )
        else:
            return None

    @property
    def db_connection_string_async(self) -> str | None:
        if self.db_connection_string is not None:
            return self.db_connection_string.replace(
                "postgresql://", "postgresql+asyncpg://"
            )
        else:
            return None

    # S3 bucket access settings for storing result files
    aws_region_name: str | None = None
    aws_s3_bucket_name: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    # name of worker executing job
    worker_name : str | None = None
    compute_cost_per_second: dict[InputSpecTypeKeys, float] = Field(default={  # noqa Pydantic creates deep copy of mutable defaults
        "pipeline": 2.0 / 3600,
        "single_mutation_scan": 2.0 / 3600,
        "protein_to_dna": 0.05 / 3600,
    })

    # time overhead due to starting of VM, container etc.
    job_time_overhead_seconds: dict[InputSpecTypeKeys, float] = Field(default={  # noqa Pydantic creates deep copy of mutable defaults
        "pipeline": 360,
        "single_mutation_scan": 360,
        "protein_to_dna": 360,
    })

    # users for which not to display result list (e.g. public access mode)
    hidden_result_list_user_ids: list[str] | None = None

    # new user settings
    new_user_default_priority: float = 1
    # NOTE: CAREFUL IF SETTING THIS TO NON-ZERO VALUE AS USED COMPUTE CREDIT MAY SPIRAL OUT OF CONTROL
    #  WITHOUT FURTHER CHECKS IN PLACE
    new_user_default_balance: float = 5

    # frontend app URL (for CORS)
    frontend_url: str = "https://evedesign.bio"

    # definitions of available models; note that outermost key used
    # here must match key of model in API schema
    models: dict[str, ModelDefinition] = Field(default={  # noqa Pydantic creates deep copy of mutable defaults
        # Gibbs sampler
        "gibbs": {
            "module_name": "evedesign.samplers.gibbs",
            "class_name": "GibbsSampler",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {
                        "require_strict_pos": True,
                    }
                }
            },
            "args_transform": transform_gibbs_args,
        },

        # Linear distance restraint
        "linear_seq_dist_restraint": {
            "module_name": "evedesign.restraints.seq_dist",
            "class_name": "LinearSeqDistRestraint",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {
                        "exclude_gaps_from_distance": True,
                    }
                }
            },
        },

        # EVmutation2 model
        "evmutation2": {
            "module_name": "evedesign.models.evmutation2",
            "class_name": "EVmutation2",
            "gpu_required": True,
            "variants": {
                "msa-only-small": {
                    "file_args": {
                        "model_file_path": "https://huggingface.co/thomashopf/evmutation2/resolve/main/msa-only-small.ckpt",
                    },
                    "args": {
                        **EVMUTATION2_DEFAULTS,
                        "model_name": "msa-only-small",
                    },
                }
            },
        },

        # ESM2 model
        "esm2": {
            "module_name": "evedesign.models.esm2",
            "class_name": "ESM2",
            "gpu_required": True,
            "variants": {
                "esm2_t33_650M_UR50D": {
                    "file_args": {
                        "model_dir_path": [
                            (
                                "config.json",
                                "https://huggingface.co/facebook/esm2_t33_650M_UR50D/resolve/main/config.json?download=true"
                            ),
                            (
                                "tokenizer_config.json",
                                "https://huggingface.co/facebook/esm2_t33_650M_UR50D/resolve/main/tokenizer_config.json?download=true"
                            ),
                            (
                                "vocab.txt",
                                "https://huggingface.co/facebook/esm2_t33_650M_UR50D/resolve/main/vocab.txt?download=true"
                            ),
                            (
                                "pytorch_model.bin",
                                "https://huggingface.co/facebook/esm2_t33_650M_UR50D/resolve/main/pytorch_model.bin?download=true"
                            ),
                        ]
                    },
                    "args": {
                        **ESM2_DEFAULTS,
                        "model_name": "esm2_t33_650M_UR50D"
                    }
                }
            },
        },

        # Boltz-2 model
        "boltz2": {
            "module_name": "evedesign.models.boltzfold",
            "class_name": "BoltzFoldTransformer",
            "gpu_required": True,
            "variants": {
                "default": {
                    "file_args": {
                        "model_dir_path": [
                            (
                                "mols.tar",
                                "https://huggingface.co/boltz-community/boltz-2/resolve/main/mols.tar"
                            ),
                            (
                                "boltz2_conf.ckpt",
                                "https://huggingface.co/boltz-community/boltz-2/resolve/main/boltz2_conf.ckpt"
                            ),
                            (
                                "boltz2_aff.ckpt",
                                "https://huggingface.co/boltz-community/boltz-2/resolve/main/boltz2_aff.ckpt"
                            ),
                        ]
                    },
                    "args": {
                        "batch_size": 64,
                        "sampling_steps": 200,
                        "diffusion_samples": 1,
                        "recycling_steps": 3,
                        "use_msa": True,
                        "score_attribute": "confidence_score",
                        "confidence_attribute":"complex_plddt",
                        "keep_model_after_build": False,
                        "device": "cuda",
                    }
                }
            },
        },

        # MixMHC2pred. Academic use only, binary and PWMdef needs to be installed in environment
        "mixmhc2pred": {
            "module_name": "evedesign.models.immunogenicity",
            "class_name": "MixMHC2Pred",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {
                        "binary": "MixMHC2pred",
                        "prediction_cache_size": 10 ** 6,
                        "cpu": None,
                    }
                }
            },
        },

        # Surface-exposed sequence liabilities
        "exposed_motif_restraint": {
            "module_name": "evedesign.restraints.motif",
            "class_name": "ExposedMotifRestraint",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {}
                }
            },
        },

        # DNAchisel codon optimizer
        "dnachisel": {
            "module_name": "evedesign.codons.dnachisel",
            "class_name": "DNAChiselCodonOptimizer",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {
                        "cpu": 1,
                    },
                }
            },
        },

        # MDS analysis
        "seqspace_mds_aligned": {
            "module_name": "evedesign.analyzers.sequence_space",
            "class_name": "SequenceSpaceMDS",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {},
                }
            },
        },

        # UMAP analysis
        "seqspace_umap_aligned": {
            "module_name": "evedesign.analyzers.sequence_space",
            "class_name": "SequenceSpaceUMAP",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {},
                }
            },
        },

        # LandmarkMDS analysis
        "seqspace_landmarkmds": {
            "module_name": "evedesign.analyzers.sequence_space",
            "class_name": "SequenceSpaceLandmarkMDS",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {},
                }
            },
        },

        # supervised scikit-learn model on top of embeddings / scores
        "supervised_sklearn_predictor": {
            "module_name": "evedesign.models.supervised",
            "class_name": "SklearnPredictorOnEmbeddingsScores",
            "gpu_required": False,
            "variants": {
                "default": {
                    "file_args": None,
                    "args": {}
                }
            },
        },

        # TODO: add more model definitions here as they become available
    })
