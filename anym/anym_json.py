import json
import logging
from pathlib import Path
from typing import List, Optional

from utils import SeededNameAnonymizer, save_mapping_json

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


def save_json_file(data, file_path: str):
    """
    Save a Python object as a JSON file.

    Parameters
    ----------
    data : dict or list
        The Python object to be saved as JSON.
    file_path : str
        The path where the JSON file will be saved.

    Raises
    ------
    IOError
        If there is an error writing to the file.
    """
    try:
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=4)
    except IOError as e:
        logger.error("Error writing to file '%s': %s", file_path, e)
        raise


def anonymize_json_data(
    input_json: str,
    anonymizer: SeededNameAnonymizer,
    categories: Optional[List[str]] = None,
):

    for entry in input_json:
        for category in categories:
            if category in entry:
                original_value = entry[category]
                anonymized_value = anonymizer.translate(original_value)
                entry[category] = anonymized_value

    return input_json


def _get_json_keys(data: dict) -> List[str]:
    all_keys = set()

    # Über alle Einträge in der Liste iterieren
    for entry in data:
        if isinstance(
            entry, dict
        ):  # Sicherstellen, dass es sich um ein Dictionary handelt
            all_keys.update(entry.keys())

        # Das Set in eine sortierte Liste umwandeln (für bessere Lesbarkeit)
        unique_keys_list = sorted(list(all_keys))
    return unique_keys_list


def anonymize_json_file(
    input_json: str,
    output_json: str,
    mapping_output: Path,
    seed: str,
    categories: Optional[List[str]] = None,
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
    categories : Optional[List[str]]
        The list of categories to anonymize.

    Raises
    ------
    FileNotFoundError
        If the input file does not exist.
    json.JSONDecodeError
        If the input file is not a valid JSON.
    """
    data = load_json_file(input_json)

    if categories:
        prefered_categories = categories
    else:
        prefered_categories = _get_json_keys(data)
    anonymizer = SeededNameAnonymizer(seed=seed, prefix="ANON_", length=10)

    anonymized_data = anonymize_json_data(data, anonymizer, prefered_categories)

    save_mapping_json(mapping_output, anonymizer)
    save_json_file(anonymized_data, output_json)
