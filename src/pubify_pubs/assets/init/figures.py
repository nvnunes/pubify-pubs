"""Figures entrypoint for publication figures."""

import matplotlib.pyplot as plt
import numpy as np

from pubify_pubs.data import load_publication_data_npz, publication_data_path, save_publication_data_npz
from pubify_pubs.decorators import data, figure

# Example pinned publication-local data loader:
# @data("example.npy")
# def load_example(ctx, path):
#     return np.load(path)


@figure
def plot_test(ctx):
    x = np.linspace(0.0, 1.0, 100)
    fig, ax = plt.subplots()
    ax.plot(x, x)
    return fig
