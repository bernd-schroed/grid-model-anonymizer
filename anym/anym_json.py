import json
import logging
from pathlib import Path
from typing import List, Optional

from utils import SeededNameAnonymizer, load_mapping_json

logger = logging.getLogger("anym_json.py")


def load_json_file(file_path: str):
    """
    Load a JSON file and return its content as a Python object.

    Parameters
    ----------
    file_path : str
        The path to the JSON file to be loaded.

    Returns
    -------
    dict or list
        The content of the JSON file as a Python dictionary or list.

    Raises
    ------
    FileNotFoundError
        If the specified file does not exist.
    json.JSONDecodeError
        If the file is not a valid JSON.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
            return data
    except FileNotFoundError:
        logger.error("File not found: '%s'", file_path)
        raise
    except json.JSONDecodeError:
        logger.error("Invalid JSON format in file: '%s'", file_path)
        raise


def anonymize_json_file(
    input_json: str,
    output_json: str,
    mapping_output: Path,
    seed: str,
):
    """
    Anonymize the content of a JSON file and save the result to another file.

    Parameters
    ----------
    input_json : str
        The path to the input JSON file to be anonymized.
    output_json : str
        The path where the anonymized JSON will be saved.
    mapping_output : Path
        The path where the mapping file for anonymization will be saved.
    seed : str
        The seed for reproducible anonymization.
    columns : Optional[List[str]]
        The list of columns to anonymize.

    Raises
    ------
    FileNotFoundError
        If the input file does not exist.
    json.JSONDecodeError
        If the input file is not a valid JSON.
    """
    data = load_json_file(input_json)

    # anonymizer = SeededNameAnonymizer(seed=seed, prefix="ANON_", length=10)

    # with open(output_json, "w", encoding="utf-8") as file:
    #     json.dump(anonymized_data, file, ensure_ascii=False, indent=4)
