from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True, slots=True)
class MLXConfig:
    model_id: str = "mlx-community/Qwen3-8B-4bit"
    base_url: str = "http://127.0.0.1:8080/v1"
    managed_server: bool = True
    server_module: str = "mlx_lm.server"
    startup_timeout_seconds: float = 600.0
    request_timeout_seconds: float = 300.0
    poll_interval_seconds: float = 0.5
    extra_server_args: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    max_context_tokens: int = 12_288
    max_output_tokens: int = 2_048
    temperature: float = 0.2
    top_p: float = 0.95
    enable_thinking: bool = False
    output_budget_policy: str = "legacy"
    max_recovery_output_tokens: int = 2048
    reset_output_tokens: int = 512
    proof_format: str = "full_file"


@dataclass(frozen=True, slots=True)
class KiminaConfig:
    base_url: str = "http://127.0.0.1:8000"
    endpoint: str = "/api/check"
    health_endpoint: str = "/health"
    request_timeout_seconds: float = 90.0
    lean_timeout_seconds: int = 60
    api_key: str | None = None
    reuse_repl: bool = True


@dataclass(frozen=True, slots=True)
class LeanLSPConfig:
    enabled: bool = False
    required: bool = True
    command: str = "lean-lsp-mcp"
    args: tuple[str, ...] = ()
    project_path: Path = Path("services/kimina-lean-server/mathlib4")
    request_timeout_seconds: float = 360.0
    max_feedback_chars: int = 8_000


@dataclass(frozen=True, slots=True)
class LeanExploreConfig:
    enabled: bool = False
    required: bool = True
    command: str = "lean-explore"
    args: tuple[str, ...] = ("mcp", "serve", "--backend", "local")
    cache_dir: Path = Path("services/lean-explore/cache")
    hf_cache_dir: Path = Path("services/lean-explore/huggingface")
    data_version: str | None = None
    limit: int = 8
    source_limit: int = 5
    rerank_top: int = 0
    packages: tuple[str, ...] = ("Mathlib", "Init")
    request_timeout_seconds: float = 600.0
    max_query_chars: int = 4_000
    max_context_chars: int = 12_000
    embedding_batch_size: int = 1
    reranker_batch_size: int = 1


@dataclass(frozen=True, slots=True)
class InformalReasoningConfig:
    enabled: bool = False
    verifier_enabled: bool = True
    invocation_policy: str = "after_model_failures"
    trigger_after_failures: int = 1
    max_refinement_rounds: int = 3
    max_context_tokens: int = 8_192
    max_output_tokens: int = 1_536
    generator_temperature: float = 0.3
    verifier_temperature: float = 0.0
    top_p: float = 0.95
    max_packet_chars: int = 12_000
    max_proof_chars: int = 6_000
    max_critique_chars: int = 4_000
    strategy_retrieval_enabled: bool = True
    strategy_query_limit: int = 2
    strategy_query_max_chars: int = 240
    rewrite_recovery_enabled: bool = True


@dataclass(frozen=True, slots=True)
class V4Config:
    enabled: bool = False
    discussion_enabled: bool = True
    fresh_context_enabled: bool = True
    compression_enabled: bool = True
    trigger_after_failures: int = 2
    max_discussion_calls: int = 1
    max_fresh_calls: int = 1
    max_context_tokens: int = 8192
    discussion_max_output_tokens: int = 1536
    fresh_max_output_tokens: int = 512
    summary_max_chars: int = 2400


@dataclass(frozen=True, slots=True)
class SpecialistConfig:
    enabled: bool = False
    model_id: str = "mlx-community/DeepSeek-Prover-V2-7B-4bit"
    trigger_after_failures: int = 1
    max_calls: int = 1
    max_context_tokens: int = 8192
    max_output_tokens: int = 2048
    temperature: float = 0.0
    top_p: float = 0.95


@dataclass(frozen=True, slots=True)
class AgentConfig:
    max_iterations: int = 5
    unload_model_after_attempt: bool = True
    diagnostics_max_chars: int = 8_000
    log_path: Path = Path("runs/attempts.jsonl")
    result_dir: Path = Path("runs/results")


@dataclass(frozen=True, slots=True)
class AppConfig:
    mlx: MLXConfig = field(default_factory=MLXConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    kimina: KiminaConfig = field(default_factory=KiminaConfig)
    lean_lsp: LeanLSPConfig = field(default_factory=LeanLSPConfig)
    lean_explore: LeanExploreConfig = field(default_factory=LeanExploreConfig)
    informal_reasoning: InformalReasoningConfig = field(
        default_factory=InformalReasoningConfig
    )
    agent: AgentConfig = field(default_factory=AgentConfig)
    v4: V4Config = field(default_factory=V4Config)
    specialist: SpecialistConfig = field(default_factory=SpecialistConfig)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"TOML section [{name}] must be a table")
    return value


def load_config(path: str | Path | None = None) -> AppConfig:
    if path is None:
        return AppConfig()
    config_path = Path(path)
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    mlx_data = _section(data, "mlx")
    if "extra_server_args" in mlx_data:
        mlx_data["extra_server_args"] = tuple(str(v) for v in mlx_data["extra_server_args"])
    generation_data = _section(data, "generation")
    kimina_data = _section(data, "kimina")
    lean_lsp_data = _section(data, "lean_lsp")
    if "args" in lean_lsp_data:
        lean_lsp_data["args"] = tuple(str(v) for v in lean_lsp_data["args"])
    if "command" in lean_lsp_data:
        command = str(lean_lsp_data["command"])
        command_path = Path(command)
        if not command_path.is_absolute() and len(command_path.parts) > 1:
            command = str((config_path.parent / command_path).resolve())
        lean_lsp_data["command"] = command
    if "project_path" in lean_lsp_data:
        project_path = Path(lean_lsp_data["project_path"])
        if not project_path.is_absolute():
            project_path = (config_path.parent / project_path).resolve()
        lean_lsp_data["project_path"] = project_path
    lean_explore_data = _section(data, "lean_explore")
    for key in ("args", "packages"):
        if key in lean_explore_data:
            lean_explore_data[key] = tuple(
                str(value) for value in lean_explore_data[key]
            )
    if "command" in lean_explore_data:
        command = str(lean_explore_data["command"])
        command_path = Path(command)
        if not command_path.is_absolute() and len(command_path.parts) > 1:
            command = str((config_path.parent / command_path).resolve())
        lean_explore_data["command"] = command
    for key in ("cache_dir", "hf_cache_dir"):
        if key in lean_explore_data:
            value = Path(lean_explore_data[key])
            if not value.is_absolute():
                value = (config_path.parent / value).resolve()
            lean_explore_data[key] = value
    informal_reasoning_data = _section(data, "informal_reasoning")
    agent_data = _section(data, "agent")
    retired = (set(agent_data) & {"fallback_enabled", "fallback_tactics"}) | (
        set(informal_reasoning_data) & {"rewrite_salvage_enabled", "rewrite_salvage_tactics", "rewrite_salvage_max_checks"})
    if retired:
        raise ValueError("Automatic tactic trials have been removed. Remove these retired settings: "
                         + ", ".join(sorted(retired)))
    if "max_rounds" in agent_data:
        if "max_iterations" in agent_data:
            raise ValueError("[agent] cannot set both max_rounds and max_iterations")
        agent_data["max_iterations"] = agent_data.pop("max_rounds")
    for path_key in ("log_path", "result_dir"):
        if path_key in agent_data:
            value = Path(agent_data[path_key])
            if not value.is_absolute():
                value = config_path.parent / value
            agent_data[path_key] = value

    config = AppConfig(
        mlx=MLXConfig(**mlx_data),
        generation=GenerationConfig(**generation_data),
        kimina=KiminaConfig(**kimina_data),
        lean_lsp=LeanLSPConfig(**lean_lsp_data),
        lean_explore=LeanExploreConfig(**lean_explore_data),
        informal_reasoning=InformalReasoningConfig(**informal_reasoning_data),
        agent=AgentConfig(**agent_data),
        v4=V4Config(**_section(data, "v4")),
        specialist=SpecialistConfig(**_section(data, "specialist")),
    )
    _validate(config)
    return config


def _validate(config: AppConfig) -> None:
    specialist = config.specialist
    if type(specialist.enabled) is not bool:
        raise ValueError("specialist.enabled must be a boolean")
    if (type(specialist.trigger_after_failures) is not int
            or not 1 <= specialist.trigger_after_failures <= 20):
        raise ValueError("specialist trigger_after_failures must be between 1 and 20")
    if type(specialist.max_calls) is not int or specialist.max_calls != 1:
        raise ValueError("Minimal V5 supports exactly one specialist invocation per theorem")
    if (type(specialist.max_context_tokens) is not int
            or not 1024 <= specialist.max_context_tokens <= 8192):
        raise ValueError("Specialist context must be between 1024 and 8192 tokens")
    if (type(specialist.max_output_tokens) is not int
            or not 0 < specialist.max_output_tokens < specialist.max_context_tokens):
        raise ValueError("Specialist output must leave room for its input packet")
    if not 0 <= specialist.temperature <= 2 or not 0 < specialist.top_p <= 1:
        raise ValueError("Invalid specialist sampling parameters")
    if not isinstance(specialist.model_id, str) or not specialist.model_id.strip():
        raise ValueError("Specialist model_id must be nonempty")
    if specialist.enabled and not config.mlx.managed_server:
        raise ValueError("V5 requires managed MLX serving to guarantee model unloading")
    if specialist.enabled and specialist.model_id == config.mlx.model_id:
        raise ValueError("Specialist and orchestrator must use different model IDs")
    generation = config.generation
    if generation.proof_format not in {"full_file", "proof_body"}:
        raise ValueError("proof_format must be full_file or proof_body")
    if generation.output_budget_policy not in {"legacy", "fixed", "adaptive"}:
        raise ValueError("output_budget_policy must be legacy, fixed or adaptive")
    for value in (generation.max_output_tokens, generation.max_recovery_output_tokens,
                  generation.reset_output_tokens):
        if type(value) is not int or value <= 0:
            raise ValueError("Formal output budgets must be positive integers")
    if generation.output_budget_policy == "adaptive":
        if generation.max_recovery_output_tokens < generation.max_output_tokens:
            raise ValueError("max_recovery_output_tokens must be at least max_output_tokens")
    effective_max = (generation.max_recovery_output_tokens
                     if generation.output_budget_policy == "adaptive" else generation.max_output_tokens)
    if effective_max >= generation.max_context_tokens:
        raise ValueError("Formal output budget must leave room for the main prompt")
    if (config.v4.enabled and generation.output_budget_policy != "legacy"
            and effective_max >= config.v4.max_context_tokens):
        raise ValueError("Formal output budget must leave room for the isolated prompt")
    v4 = config.v4
    if not 1 <= v4.trigger_after_failures <= 20:
        raise ValueError("V4 trigger_after_failures must be between 1 and 20")
    if not 1 <= v4.max_discussion_calls <= 3 or not 1 <= v4.max_fresh_calls <= 3:
        raise ValueError("V4 role call limits must be between 1 and 3")
    if not 1024 <= v4.max_context_tokens <= 8192:
        raise ValueError("V4 isolated context must be between 1024 and 8192 tokens")
    for budget in (v4.discussion_max_output_tokens, v4.fresh_max_output_tokens):
        if not 1 <= budget < v4.max_context_tokens:
            raise ValueError("V4 output budget must be positive and smaller than its context")
    if not 128 <= v4.summary_max_chars <= 4000:
        raise ValueError("V4 summary_max_chars must be between 128 and 4000")
    if config.generation.max_context_tokens > 12_288:
        raise ValueError("The main-agent context may not exceed 12,288 tokens")
    if config.generation.max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be positive")
    if config.generation.max_output_tokens >= config.generation.max_context_tokens:
        raise ValueError("max_output_tokens must be smaller than max_context_tokens")
    if config.agent.max_iterations <= 0:
        raise ValueError("max_iterations must be positive")
    if config.lean_lsp.request_timeout_seconds <= 0:
        raise ValueError("Lean-LSP-MCP request timeout must be positive")
    if config.lean_lsp.max_feedback_chars <= 0:
        raise ValueError("Lean-LSP-MCP feedback limit must be positive")
    retrieval = config.lean_explore
    if retrieval.limit <= 0 or retrieval.source_limit <= 0:
        raise ValueError("LeanExplore result limits must be positive")
    if retrieval.source_limit > retrieval.limit:
        raise ValueError("LeanExplore source_limit may not exceed limit")
    if retrieval.rerank_top < 0:
        raise ValueError("LeanExplore rerank_top may not be negative")
    if retrieval.request_timeout_seconds <= 0:
        raise ValueError("LeanExplore request timeout must be positive")
    if retrieval.max_query_chars <= 0 or retrieval.max_context_chars <= 0:
        raise ValueError("LeanExplore text limits must be positive")
    if retrieval.embedding_batch_size <= 0 or retrieval.reranker_batch_size <= 0:
        raise ValueError("LeanExplore batch sizes must be positive")
    informal = config.informal_reasoning
    if informal.invocation_policy not in {"always", "after_model_failures"}:
        raise ValueError(
            "informal invocation_policy must be 'always' or 'after_model_failures'"
        )
    if informal.trigger_after_failures <= 0:
        raise ValueError("informal trigger_after_failures must be positive")
    if not 1 <= informal.max_refinement_rounds <= 5:
        raise ValueError("informal max_refinement_rounds must be between 1 and 5")
    if informal.max_context_tokens > 8_192:
        raise ValueError("isolated informal contexts may not exceed 8,192 tokens")
    if informal.max_output_tokens <= 0:
        raise ValueError("informal max_output_tokens must be positive")
    if informal.max_output_tokens >= informal.max_context_tokens:
        raise ValueError(
            "informal max_output_tokens must be smaller than max_context_tokens"
        )
    if informal.max_packet_chars <= 0 or informal.max_proof_chars <= 0:
        raise ValueError("informal text limits must be positive")
    if informal.max_critique_chars <= 0:
        raise ValueError("informal critique limit must be positive")
    if not 1 <= informal.strategy_query_limit <= 3:
        raise ValueError("strategy_query_limit must be between 1 and 3")
    if not 1 <= informal.strategy_query_max_chars <= 500:
        raise ValueError("strategy_query_max_chars must be between 1 and 500")
    if not 0 <= informal.generator_temperature <= 2:
        raise ValueError("informal generator_temperature must be between 0 and 2")
    if not 0 <= informal.verifier_temperature <= 2:
        raise ValueError("informal verifier_temperature must be between 0 and 2")
    if not 0 < informal.top_p <= 1:
        raise ValueError("informal top_p must be in (0, 1]")
