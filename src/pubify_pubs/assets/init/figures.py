"""Figures entrypoint for publication figures."""

import matplotlib.pyplot as plt

from pubify_pubs import FigureExport
from pubify_pubs.decorators import data, figure, stat

# Data

# pubs:data-stub:start
@data("path/to/file")
def load_<data-id>(ctx, file_path):
    return {
        "x": [1, 2, 3],
        "y": [1, 2, 3],
    }
# pubs:data-stub:end


# Figures & Stats

# pubs:figure-stub:start
@figure
def plot_<figure-id>(ctx, example_data):
    fig, ax = plt.subplots()
    ax.scatter(example_data["x"], example_data["y"])
    return FigureExport(
        fig,
        layout="one",
    )
# pubs:figure-stub:end


# pubs:stat-stub:start
@stat
def compute_<stat-id>(ctx, example_data):
    y_values = example_data["y"]
    return {
        "Count": str(len(example_data["x"])),
        "Mean": str(sum(y_values) / len(y_values)),
    }
# pubs:stat-stub:end
