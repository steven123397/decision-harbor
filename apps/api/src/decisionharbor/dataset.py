from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys


TABLE_LOAD_ORDER = (
    "customers",
    "product_categories",
    "products",
    "orders",
    "order_items",
)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DatasetContract:
    root: Path
    contract: dict
    manifest: dict
    contract_sha256: str
    manifest_sha256: str

    @property
    def allowed_tables(self) -> set[str]:
        return {table["name"] for table in self.contract["tables"]}

    @property
    def policy_version(self) -> str:
        return f"sql-policy-v1:{self.contract['version']}:{self.contract_sha256}"


def load_dataset(root: Path) -> DatasetContract:
    contract_path = root / "contract.json"
    manifest_path = root / "manifest.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contract_hash = file_sha256(contract_path)
    if manifest["contract_sha256"] != contract_hash:
        raise ValueError("dataset contract hash does not match manifest")
    if manifest["generator_sha256"] != file_sha256(root / "generate.py"):
        raise ValueError("dataset generator hash does not match manifest")
    if contract["dataset"] != manifest["dataset"] or contract["version"] != manifest["version"]:
        raise ValueError("dataset identity does not match manifest")
    if {table["name"] for table in contract["tables"]} != set(TABLE_LOAD_ORDER):
        raise ValueError("dataset contract contains unexpected tables")
    for table in TABLE_LOAD_ORDER:
        info = manifest["files"][table]
        if file_sha256(root / info["path"]) != info["sha256"]:
            raise ValueError(f"dataset file hash mismatch: {table}")
        if contract["expected_counts"][table] != info["rows"]:
            raise ValueError(f"dataset row-count contract mismatch: {table}")
    return DatasetContract(
        root=root,
        contract=contract,
        manifest=manifest,
        contract_sha256=contract_hash,
        manifest_sha256=file_sha256(manifest_path),
    )


def run_public_validator(root: Path) -> None:
    subprocess.run([sys.executable, str(root / "validate.py")], check=True)
