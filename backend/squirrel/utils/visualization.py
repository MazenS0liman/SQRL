# ——————————————————————————————————————————————————————————————
# Imports
import matplotlib
matplotlib.use('Agg')
import numpy as np
import matplotlib.pyplot as plt
from typing import Sequence, Optional

# ——————————————————————————————————————————————————————————————
# Visualization utilities for time series data
def plot_series_with_predictions(
    series: Sequence[float],
    predictions: Sequence[float],
    title: Optional[str] = None,
    save_path: Optional[str] = None,
    show: bool = False,
) -> None:
    """
    Plot a time series and appended future predictions.
    
    **Description**:
    
        This function visualizes a historical time series and its future predictions on the same plot.
        
    :param series: A 1D sequence of historical values.
    :type series: Sequence[float]
    
    :param predictions: A 1D sequence of predicted future values.
    :type predictions: Sequence[float]
    
    :param title: Optional title for the plot.
    :type title: Optional[str]    
    
    :param save_path: If provided, the plot will be saved to this path.
    :type save_path: Optional[str]
    
    :param show: If True, the plot will be displayed using plt.show() (not recommended in non-interactive environments).
    :type show: bool
    
    :return: None
    :rtype: None
    """
    series = np.asarray(series, dtype=float)
    preds = np.asarray(predictions, dtype=float)

    plt.figure(figsize=(10, 4))
    plt.plot(series, label='history', color='C0')

    if preds.size:
        pred_x = np.arange(len(series) - 1, len(series) + len(preds))
        pred_y = np.concatenate([[series[-1]], preds])

        plt.plot(
            pred_x,
            pred_y,
            marker='o',
            linestyle='-',
            label='predictions',
            color='C1'
        )

    plt.axvline(x=len(series) - 1, color='k', linestyle='--', alpha=0.3)
    plt.legend()
    if title:
        plt.title(title)
    plt.xlabel('Time')
    plt.ylabel('Value')
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path)
    if show:
        plt.show()
    plt.close()
