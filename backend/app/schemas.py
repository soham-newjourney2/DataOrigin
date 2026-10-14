from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ColumnSchema(BaseModel):
    name: str
    dtype: str


class VersionOut(ORM):
    id: int
    dataset_id: int
    version_no: int
    parent_version_id: int | None
    file_format: str
    sha256: str
    size_bytes: int
    row_count: int
    col_count: int
    schema_: list[ColumnSchema] = Field(alias="schema_json")  # 'schema_json' shadows a BaseModel method
    note: str | None
    created_at: str


class DatasetOut(ORM):
    id: int
    name: str
    description: str | None
    source: str | None
    created_at: str


class DatasetDetail(DatasetOut):
    versions: list[VersionOut]


class DatasetListItem(DatasetOut):
    version_count: int
    latest_version: VersionOut | None


class PreviewOut(BaseModel):
    columns: list[str]
    rows: list[list]


class VerifyLocalOut(BaseModel):
    stored: str
    current: str | None
    match: bool


class AuditEventOut(ORM):
    id: int
    event_type: str
    entity_type: str
    entity_id: int
    payload_json: dict
    prev_hash: str
    event_hash: str
    created_at: str


class AuditVerifyOut(BaseModel):
    valid: bool
    checked: int
    broken_at: int | None


class QualityReportOut(ORM):
    id: int
    version_id: int
    score: float
    report_json: dict
    created_at: str


class DriftOut(BaseModel):
    version_id: int
    against_version_id: int
    schema_: dict = Field(alias="schema")
    columns: list[dict]
    summary: dict


class TransformIn(BaseModel):
    ops: list  # validated by services.transforms.normalize_ops for readable 422s
    note: str | None = None


class TransformOut(VersionOut):
    ops: list[dict]
    quality_score: float


class ExperimentIn(BaseModel):
    version_id: int
    name: str = Field(min_length=1, max_length=200)
    task: str
    algorithm: str
    target_column: str
    hyperparams: dict = Field(default_factory=dict)
    seed: int = 42
    test_size: float = 0.2


class ExperimentOut(ORM):
    id: int
    name: str
    version_id: int
    task: str
    algorithm: str
    target_column: str
    hyperparams_json: dict
    seed: int
    test_size: float
    metrics_json: dict | None
    env_json: dict | None
    model_sha256: str | None
    commitment_sha256: str | None
    status: str
    error: str | None
    duration_ms: int | None
    reproduced_from_id: int | None
    created_at: str


class ReproduceOut(BaseModel):
    original_id: int
    new_experiment_id: int
    match: bool
    metric_diffs: dict
    dataset_hash_match: bool
    experiment: ExperimentOut


class ChainStatusOut(BaseModel):
    enabled: bool
    connected: bool
    chain_id: int | None
    contract_address: str | None
    contract_deployed: bool
    redeploy_needed: bool
    error: str | None
    network: str | None = None    # RPC host only (never the full URL, which may contain an API key)
    explorer: str | None = None   # block explorer base URL for known public networks
    signer: str | None = None     # public address that signs registrations (public-network mode)


class ChainRegisterIn(BaseModel):
    artifact_type: str
    artifact_id: int


class ChainRecordOut(ORM):
    id: int
    artifact_type: str
    artifact_id: int
    sha256: str
    status: str
    tx_hash: str | None
    block_number: int | None
    chain_id: int | None
    contract_address: str | None
    error: str | None
    created_at: str
    registered_at: str | None


class ChainRetryOut(BaseModel):
    retried: int
    reanchored: int
    registered: int
    records: list[ChainRecordOut]


class OnChainOut(BaseModel):
    registered: bool
    timestamp: str | None
    registrant: str | None
    tx_hash: str | None
    explorer: str | None = None


class ChainVerifyOut(BaseModel):
    artifact_type: str
    artifact_id: int
    current_sha: str | None
    db_sha: str
    local_match: bool
    on_chain: OnChainOut | None
    verdict: str
    chain_status: str
    chain_error: str | None
    chain_reset: bool
    note: str | None
