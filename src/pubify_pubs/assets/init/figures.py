"""Figures entrypoint for publication figures."""

import matplotlib.pyplot as plt
import numpy as np

from pubify_pubs import FigureResult, StatResult, TableResult
from pubify_data import data, figure, stat, table

# Data

# pubs:data-stub:start
@data("path/to/file")
def load_<data-id>(ctx, file_path):
    return {
        "x": np.array([1, 2, 3]),
        "y": np.array([1, 2, 3]),
    }
# pubs:data-stub:end


# Figures, Stats & Tables

# pubs:figure-stub:start
@figure
def plot_<figure-id>(ctx, example_data):
    fig, ax = plt.subplots()
    ax.scatter(example_data["x"], example_data["y"])
    return FigureResult(
        fig,
        layout="one",
    )
# pubs:figure-stub:end


# pubs:stat-stub:start
@stat
def compute_<stat-id>(ctx, example_data):
    return StatResult({
        "Count": str(example_data["x"].size),
        "Mean": str(example_data["y"].mean()),
    })
# pubs:stat-stub:end


# pubs:table-stub:start
@table
def tabulate_<table-id>(ctx, example_data):
    return TableResult(
        np.column_stack((example_data["x"], example_data["y"])),
        formats=["{}", "{}"],
    )
# pubs:table-stub:end
