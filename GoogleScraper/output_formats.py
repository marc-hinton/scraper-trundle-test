# -*- coding: utf-8 -*-

"""Results-serialiser helpers.

These functions turn a list of result dictionaries (as produced by
GoogleScraper.output_converter.row2dict) into a single string, either
as JSON or as CSV. They are used by run.py (via GoogleScraper.core) to
optionally dump the collected results to stdout in the requested
--output-format after a scrape job has finished.
"""

import csv
import io
import json


def to_json(results):
    """Serialise a list of result dictionaries to a JSON formatted string.

    Args:
        results: A list of dictionaries (one per scraped link/result).

    Returns:
        A string containing the JSON representation of results.
    """
    return json.dumps(results, indent=2, sort_keys=True, default=str)


def to_csv(results):
    """Serialise a list of result dictionaries to a CSV formatted string.

    Args:
        results: A list of dictionaries (one per scraped link/result).

    Returns:
        A string containing the CSV representation of results. If
        results is empty, an empty string is returned.
    """
    if not results:
        return ''

    fieldnames = sorted({key for row in results for key in row.keys()})

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()

    for row in results:
        writer.writerow(row)

    return buffer.getvalue()
