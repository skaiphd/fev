import reprlib

import datasets
import pandas as pd

from .constants import DEFAULT_NUM_PROC

__all__ = [
    "infer_column_types",
    "validate_time_series_dataset",
]


def validate_time_series_dataset(
    dataset: datasets.Dataset,
    id_column: str = "id",
    timestamp_column: str = "timestamp",
    num_proc: int = DEFAULT_NUM_PROC,
    required_columns: list[str] | None = None,
    num_records_to_validate: int | None = None,
) -> None:
    """Ensure that `datasets.Dataset` object is a valid time series dataset.

    This methods validates the following assumptions:

    - `id_column` is present and has type `Value('string')`
    - all values in the `id_column` are unique
    - `timestamp_column` is present and has type `Sequence(Value('timestamp'))`
    - at least 1 dynamic column is present

    Following checks are performed for the first `num_records_to_validate` records in the dataset (or for all records
    if `num_records_to_validate=None`)

    - timestamps have a regular frequency that can be inferred with pandas.infer_freq
    - values in all dynamic columns have same length as timestamp_column

    Parameters
    ----------
    dataset
        Dataset that must be validated.
    id_column
        Name of the column containing the unique ID of each time series.
    timestamp_column
        Name of the column containing the timestamp of time series observations.
    """
    if required_columns is None:
        required_columns = []
    required_columns += [id_column, timestamp_column]
    missing_columns = set(required_columns).difference(set(dataset.column_names))
    if len(missing_columns) > 0:
        raise AssertionError(
            f"Following {len(missing_columns)} columns are missing from the dataset: {reprlib.repr(missing_columns)}. "
            f"Available columns: {dataset.column_names}"
        )

    id_feature = dataset.features[id_column]
    if not isinstance(id_feature, datasets.Value):
        raise AssertionError(f"id_column {id_column} must have type Value")
    timestamp_feature = dataset.features[timestamp_column]
    if not (
        isinstance(timestamp_feature, datasets.Sequence) and timestamp_feature.feature.dtype.startswith("timestamp")
    ):
        raise AssertionError(f"timestamp_column {timestamp_column} must have type Sequence(Value('timestamp'))")

    if len(set(dataset[id_column])) != len(dataset[id_column]):
        raise AssertionError(f"ID column {id_column} must contain unique values for each record")

    dynamic_columns, static_columns = infer_column_types(
        dataset,
        id_column,
        timestamp_column,
    )

    if len(dynamic_columns) == 0:
        raise AssertionError("Dataset must contain at least a single dynamic column of type Sequence")

    if num_records_to_validate is not None:
        dataset = dataset.select(range(num_records_to_validate))
    dataset.map(
        _validate_dynamic_columns,
        num_proc=min(num_proc, len(dataset)),
        desc="Validating dataset format",
        fn_kwargs={"id_column": id_column, "timestamp_column": timestamp_column, "dynamic_columns": dynamic_columns},
    )


def _validate_dynamic_columns(record: dict, id_column: str, timestamp_column: str, dynamic_columns: list[str]) -> None:
    """Validate dynamic columns for a single record."""
    timestamps = record[timestamp_column]
    if pd.infer_freq(timestamps) is None:
        raise AssertionError(f"pd.infer_freq failed to infer timestamp frequency for record {record[id_column]}.")
    for col in dynamic_columns:
        if len(record[col]) != len(timestamps):
            raise AssertionError(
                f"Length of dynamic column {col} doesn't match the length of the timestamp column for record {record[id_column]}"
            )


def infer_column_types(
    dataset: datasets.Dataset,
    id_column: str,
    timestamp_column: str,
) -> tuple[list[str], list[str]]:
    """Infer the types of columns in a time series dataset.

    Columns that have type `datasets.Sequence` are interpreted as dynamic features, and all remaining columns except
    `id_column` and `timestamp_column` are interpreted as static features.

    Parameters
    ----------
    dataset
        Time series dataset.
    id_column : str
        Name of the column with the unique identifier of each time series.
    timestamp_column : str
        Name of the column with the timestamps of the observations.

    Returns
    -------
    dynamic_columns : List[str]
        Names of columns that contain dynamic (time-varying) features.
    static_columns : List[str]
        Names of columns that contain static (time-independent) features.
    """
    dynamic_columns = []
    static_columns = []
    for col_name, col_type in dataset.features.items():
        if col_name not in [id_column, timestamp_column]:
            if isinstance(col_type, datasets.Sequence):
                dynamic_columns.append(col_name)
            else:
                static_columns.append(col_name)
    return dynamic_columns, static_columns


class PatchedDownloadConfig(datasets.DownloadConfig):
    # Fixes a bug that prevents `load_dataset` from loading datasets from S3.
    # See https://github.com/huggingface/datasets/issues/6598
    def __post_init__(self, use_auth_token):
        if use_auth_token != "deprecated":
            self.token = use_auth_token
