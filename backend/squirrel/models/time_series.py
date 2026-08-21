# ——————————————————————————————————————————————————————————————
# Imports
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from typing import Tuple, Optional
import tensorflow as tf
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error

tf.random.set_seed(123)
np.random.seed(123)

# ——————————————————————————————————————————————————————————————
# Time Series Predictor Base class
class TimeSeriesPredictor(ABC):
    def __init__(self):
        pass

    @abstractmethod
    def predict(self, X):
        pass

    def load_data(
        self,
        data_path: str,
        target_column: str,
    ):
        """
        Load the time series data from the specified path and compute the first difference. 
        This method reads the time series data from a CSV file, selects the target column, computes the first difference of the target variable, and scales the differenced data for model training.

        **Description:**

            The `load_data` method takes the path to the data file and the name of the target column as input. 
            It reads the data from a CSV file into a pandas DataFrame, selects the specified target column, and computes the first difference of the target variable to capture the changes in the time series. 
            The differenced data is then scaled using a `StandardScaler` to ensure that it is on a similar scale for model training. 
            The method returns both the original target data and the scaled first difference, which are used in subsequent steps of the modeling process.

        :param data_path: The file path to the CSV file containing the time series data.
        :type data_path: str

        :param target_column: The name of the column in the CSV file that contains the target variable for prediction.
        :type target_column: str

        :return: A tuple containing the original target data and the scaled first difference of the target variable.
        :rtype: Tuple[np.ndarray, np.ndarray]
        """
        if data_path.endswith('.csv'):
            # Load CSV file
            df = pd.read_csv(data_path)
                        
            # Select the target column
            target_data = df[target_column].values.astype(float)
            
            # Calculate the nth difference
            target_data_diff = np.diff(target_data, n=self.difference_order)
            
            # Scale the target data
            target_data_diff_scaled = self.scaler.fit_transform(target_data_diff.reshape(-1, 1)).flatten()

            return target_data, target_data_diff_scaled
        else:
            raise ValueError("Unsupported file format. Please provide a CSV file.")

# ——————————————————————————————————————————————————————————————
# BiLSTM Predictor for Time Series
class BiLSTMPredictor(TimeSeriesPredictor):
    """
    A BiLSTM-based predictor for time series data that incorporates differencing to handle non-stationarity. 
    This class implements a BiLSTM model for time series prediction, which includes methods for loading data, preparing it for training, fitting the model, evaluating its performance, and making predictions.
    
    **Description:**

        The `BiLSTMPredictor` class is designed to handle time series data by first applying differencing to make the series stationary, which is a common requirement for many time series forecasting models. 
        The class includes methods to load data from a CSV file, prepare the data by creating input sequences and corresponding target values, split the data into training and testing sets, fit a BiLSTM model to the training data, evaluate the model's performance on the test set, and make future predictions based on the trained model.
        The BiLSTM architecture allows the model to capture both past and future dependencies in the time series data, making it well-suited for forecasting tasks.
        
    **Example Usage:**
    
    .. code-block:: python
    
        from kraken.models.time_series import BiLSTMPredictor
        
        predictor = BiLSTMPredictor(
            window_size=24,
            data_path="./notebooks/data/AAPL.csv",
            target_column="Close"
        )
        
        predictor()
        
        predictions = predictor.predict(n_steps=5)
        # Output
        # [150.25, 151.30, 152.10, 153.00, 154.20]
    """
    def __init__(
        self,
        window_size: int = 30,
        difference_order: int = 1,
        data_path: str = None,
        target_column: str = None,
    ):
        super().__init__()
        if difference_order < 1:
            raise ValueError("difference_order must be at least 1")

        self.scaler = StandardScaler()
        self.window_size = window_size
        self.difference_order = difference_order
        self.data_path = data_path
        self.target_column = target_column
        self.model = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(window_size, 1)),
            tf.keras.layers.Bidirectional(tf.keras.layers.LSTM(64, return_sequences=True)),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Bidirectional(tf.keras.layers.LSTM(32)),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(1)
        ])

        self.model.compile(optimizer='adam', loss='mse', metrics=['mae'])

    def _inverse_difference(self, base_values, difference_value):
        base_values = np.asarray(base_values, dtype=float).reshape(-1)
        if base_values.size < self.difference_order:
            raise ValueError(
                f"Expected at least {self.difference_order} base values, got {base_values.size}"
            )

        current_value = float(np.asarray(difference_value, dtype=float).reshape(-1)[0])
        for order in range(self.difference_order - 1, -1, -1):
            if order == 0:
                current_value = base_values[-1] + current_value
            else:
                current_value = np.diff(base_values, n=order)[-1] + current_value

        return current_value

    def prepare_data(
        self,
        data: np.ndarray,
        data_diff: np.ndarray,
    ):
        """
        Prepare the data for training the BiLSTM model by creating input sequences and corresponding target values.
        This method takes the original time series data and its first difference, 
        and constructs input sequences of a specified window size along with their corresponding target values.
        
        **Description:**
        
            The `prepare_data` method iterates through the first difference of the time series data, starting from the index defined by the window size.
            For each index, it creates an input sequence consisting of the previous `window_size` values of the first difference, and a corresponding target value which is the next value in the first difference.
            Additionally, it keeps track of the base values (the original time series values) corresponding to each input sequence, which are used to reconstruct the predicted values from the predicted differences. 
            The method returns four arrays: the input sequences of the first difference, the target values of the first difference, the base values corresponding to each input sequence, and the actual target values corresponding to each input sequence.
        
        :param data: The original time series data as a 1D numpy array.
        :type data: np.ndarray
        
        :param data_diff: The first difference of the time series data, scaled for model training.
        :type data_diff: np.ndarray
        
        :return: A tuple containing the input sequences of the first difference, the target values of the first difference, the base values corresponding to each input sequence, and the actual target values corresponding to each input sequence.
        :rtype: Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        
        """
        X_diff, y_diff = [], []
        base_values, target_values = [], []
    
        for idx in range(self.window_size, len(data_diff)):
            X_diff.append(data_diff[idx - self.window_size:idx].reshape(self.window_size, 1))
            y_diff.append(data_diff[idx])
            base_values.append(data[idx:idx + self.difference_order])
            target_values.append(data[idx + self.difference_order])

        X_diff = np.array(X_diff)
        y_diff = np.array(y_diff)
        base_values = np.array(base_values)
        target_values = np.array(target_values)
        
        return X_diff, y_diff, base_values, target_values

    def split_data(
        self,
        X_diff: np.ndarray,
        y_diff: np.ndarray,
        base_values: np.ndarray,
        target_values: np.ndarray,
        train_ratio: float = 0.8,
    ):
        """
        Split the data into training and testing sets based on the specified train-test ratio.
        This method divides the input data into training and testing subsets, ensuring that the temporal order of the time series is preserved. 
        The training set consists of the initial portion of the data, while the testing set contains the remaining portion.
        
        **Description:**
        
            The `split_data` method takes the input features, target values, base values, and target values, along with a train-test ratio.
            It calculates the number of samples to be included in the training set based on the total number of samples and the specified train-test ratio.
            The method then slices the input data into training and testing subsets accordingly, ensuring that the temporal order of the time series is maintained. 
            The training set includes the initial portion of the data, while the testing set contains the remaining portion. 
            Finally, it returns the training and testing datasets as tuples.
        
        :param X_diff: The input features for the model, typically the first difference of the target variable.
        :type X_diff: np.ndarray
        
        :param y_diff: The target values for the model, typically the first difference of the target variable.
        :type y_diff: np.ndarray
        
        :param base_values: The base values corresponding to the input features, which are used to reconstruct the predicted values from the predicted differences.
        :type base_values: np.ndarray
        
        :param target_values: The actual target values corresponding to the input features, which are used to evaluate the predictions.
        :type target_values: np.ndarray
        
        :param train_ratio: The ratio of the data to be used for training. Default is 0.8 (80% training, 20% testing).
        :type train_ratio: float
        
        """
        total_samples = len(X_diff)
        train_samples = int(total_samples * train_ratio)

        X_train_diff = X_diff[:train_samples]
        y_train_diff = y_diff[:train_samples]
        base_train = base_values[:train_samples]
        target_train = target_values[:train_samples]

        X_test_diff = X_diff[train_samples:]
        y_test_diff = y_diff[train_samples:]
        base_test = base_values[train_samples:]
        target_test = target_values[train_samples:]

        return (X_train_diff, y_train_diff, base_train, target_train), (X_test_diff, y_test_diff, base_test, target_test)

    def fit(
        self,
        X_train,
        y_train, 
        X_test, 
        y_test,
        epochs: int = 50,
        batch_size: int = 32,
        verbose: int = 1
    ) -> None:
        """
        Fit the BiLSTM model to the training data.
        This method trains the model using the provided training data and validates it on the test set to monitor for overfitting.
        It uses early stopping to halt training if the validation loss does not improve for a specified number of epochs, restoring the best weights found during training.
        
        **Description:**
        
            The `fit` method takes the training and testing datasets, along with training parameters such as the number of epochs, batch size, and verbosity level.
            It trains the BiLSTM model on the training data while validating on the test set. The method incorporates early stopping to prevent overfitting, which monitors the validation loss and stops training if it does not improve for 10 consecutive epochs, restoring the best weights found during training.
            
        :param X_train: The input features for the training set, typically the first difference of the target variable.
        :type X_train: np.ndarray
        
        :param y_train: The target values for the training set, typically the first difference of the target variable.
        :type y_train: np.ndarray
        
        :param X_test: The input features for the test set, typically the first difference of the target variable.
        :type X_test: np.ndarray
        
        :param y_test: The target values for the test set, typically the first difference of the target variable.   
        :type y_test: np.ndarray
        
        :param epochs: The number of epochs to train the model. Default is 50.
        :type epochs: int
        
        :param batch_size: The batch size to use during training. Default is 32.
        :type batch_size: int
        
        :param verbose: The verbosity level for training output. Default is 1 (progress bar).
        :type verbose: int
        
        :return: None
        :rtype: None
        """
        self.model.fit(
            X_train, y_train,
            epochs=epochs,
            batch_size=batch_size,
            validation_data=(X_test, y_test),
            verbose=verbose,
            callbacks=[
                tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True, verbose=verbose),
            ]
        )
        
    def eval(
        self, 
        X_test, 
        y_test, 
        base_test, 
        target_test
    ) -> Tuple[float, float]:
        """
        Evaluate the model's performance on the test set.
        This method predicts the target values for the test set, inverse scales the predictions, and calculates the Mean Squared Error (MSE) and Mean Absolute Error (MAE) between the predicted values and the actual target values.
        
        **Description:**
        
            The `eval` method takes the test data and the corresponding base values and target values. It uses the trained model to predict the first difference of the target variable, then inverse scales the predictions
            to get the actual predicted values. Finally, it computes the MSE and MAE between the predicted values and the actual target values, prints the results, and returns the MSE and MAE as a tuple.
        
        :param X_test: The input features for the test set, typically the first difference of the target variable.
        :type X_test: np.ndarray
        
        :param y_test: The actual target values for the test set, typically the first difference of the target variable.
        :type y_test: np.ndarray
        
        :param base_test: The base values corresponding to the test set, which are used to reconstruct the predicted values from the predicted differences.
        :type base_test: np.ndarray
        
        :param target_test: The actual target values for the test set, which are used to evaluate the predictions.
        :type target_test: np.ndarray
        
        :return: A tuple containing the Mean Squared Error (MSE) and Mean Absolute Error (MAE) of the predictions on the test set.
        :rtype: Tuple[float, float]
        
        """
        # Predict the differenced series using the model
        y_pred_diff = self.model.predict(X_test).flatten()
        
        # Inverse scale the predicted differences
        y_pred_diff_unscaled = self.scaler.inverse_transform(y_pred_diff.reshape(-1, 1)).flatten()
        
        # Reconstruct the final predicted values from the stored raw history
        y_pred = np.array([
            self._inverse_difference(base, diff)
            for base, diff in zip(base_test, y_pred_diff_unscaled)
        ])
        
        mse = mean_squared_error(target_test, y_pred)
        mae = mean_absolute_error(target_test, y_pred)
        
        print(f"Test MSE: {mse:.4f}, Test MAE: {mae:.4f}")
        
        return mse, mae
    
    def __call__(
        self
    ) -> None:
        """
        Run the entire pipeline for training and evaluating the model. 
        This method orchestrates the loading of data, preparation of training and testing datasets, fitting the model, and evaluating its performance on the test set.
        
        **Description:**
        
            The `__call__` method is designed to be a convenient entry point for executing the full workflow of the time series prediction process.
            When invoked, it performs the following steps in sequence:
            - Loads the time series data from the specified path and computes the first difference, which is then scaled for model training.
            - Prepares the training and testing datasets by creating input sequences of the specified window size and corresponding target values.
            - Splits the prepared data into training and testing sets based on the defined train-test ratio.
            - Fits the BiLSTM model using the training data, while also validating on the test set to monitor for overfitting.
            - Evaluates the model's performance on the test set by calculating and printing the Mean Squared Error (MSE) and Mean Absolute Error (MAE) between the predicted values and the actual target values.

        """
        # Load data
        data, data_diff_scaled = self.load_data(self.data_path, self.target_column)
        
        # Prepare data
        X_diff, y_diff, base_values, target_values = self.prepare_data(data, data_diff_scaled, self.window_size)
        
        # Split data
        (X_train_diff, y_train_diff, base_train, target_train), (X_test_diff, y_test_diff, base_test, target_test) = self.split_data(X_diff, y_diff, base_values, target_values)
        
        # Fit model
        self.fit(X_train_diff, y_train_diff, X_test_diff, y_test_diff)
        
        # Evaluate model
        self.eval(X_test_diff, y_test_diff, base_test, target_test)

    def predict(
        self, 
        X: Optional[np.ndarray] = None,
        n_steps: int = 1
    ) -> np.ndarray:
        """
        Predict future values based on the input data and the trained model. 
        The prediction is done iteratively, where each predicted value is fed back into the model to predict the next value.
        
        **Description:**
        
            The `predict` method takes the input data `X` and the number of future steps to predict. 
            It computes the first difference of the input data, scales it, and uses the trained model to predict the difference. 
            The predicted difference is then inverse scaled and added to the last value of the input to get the final prediction. 
            This process is repeated iteratively for the specified number of steps, with each new prediction being added to the input for the next prediction.

        :param X: The input data for prediction, typically the most recent window of data.
        :param n_steps: The number of future steps to predict.        
        
        :return: An array of predicted values for the next `n_steps` time points.
        :rtype: np.ndarray
        
        .. note::
        
            - The input `X` should be a 1D array of the most recent time series values, with a length equal to or less than the `window_size` used during training. 
            - If the input length is less than the `window_size`, it will be padded with the last value to match the required input shape for the model.
        
        """
        if X is None:
            X = self.load_data(self.data_path, self.target_column)[0]
        
        output = []
        current_input = np.asarray(X, dtype=float).copy()
        required_length = self.window_size + self.difference_order
        
        for _ in range(n_steps):
            # Crop or pad to the minimum length needed to produce one full differenced window.
            current_window = current_input[-required_length:]
            if len(current_window) < required_length:
                padding = np.full((required_length - len(current_window),), current_window[-1])
                current_window = np.concatenate([current_window, padding])

            # Compute the nth difference
            X_diff = np.diff(current_window, n=self.difference_order)
            
            # Scale the difference
            X_diff_scaled = self.scaler.transform(X_diff.reshape(-1, 1)).reshape(1, self.window_size, 1)
            
            # Predict the difference
            y_pred_diff = self.model.predict(X_diff_scaled, verbose=0).flatten()
            
            # Inverse scale the predicted difference
            y_pred_diff_unscaled = self.scaler.inverse_transform(y_pred_diff.reshape(-1, 1)).flatten()
            
            # Reconstruct the next raw value from the predicted differenced value
            y_pred = self._inverse_difference(current_window[-self.difference_order:], y_pred_diff_unscaled)
            output.append(y_pred)
            
            # Update current_input for the next iteration
            current_input = np.append(current_input, y_pred)
            
        return output
    
    def save_model(self, file_path: str):
        self.model.save(file_path)
        
    def load_model(self, file_path: str):
        self.model = tf.keras.models.load_model(file_path)
        
    def summary(self):
        self.model.summary()
    
    def get_config(self):
        """
        Get the current configuration of the model, including window size, difference order, data path, and target column.
        This method returns a dictionary containing the current configuration parameters of the model, which can be useful
        for tracking the model's settings or for saving and loading configurations.
        
        **Description:**
        
            The `get_config` method compiles the current configuration of the model into a dictionary format.
            The dictionary includes key parameters such as `window_size`, `difference_order`, `data_path`, and `target_column`, which define how the model processes the time series data and what data it uses for training and prediction.
            
        :return: A dictionary containing the current configuration parameters of the model.
        :rtype: dict
        """
        
        return {
            "window_size": self.window_size,
            "difference_order": self.difference_order,
            "data_path": self.data_path,
            "target_column": self.target_column
        }
        
    def set_config(self, config):
        """
        Update the model configuration with the provided values.
        
        **Description:**
        
            The `set_config` method allows for updating the model's configuration parameters such as `window_size`, `difference_order`, `data_path`, and `target_column`.
            It takes a dictionary `config` as input, where each key corresponds to a configuration parameter.
            The method checks for the presence of each parameter in the input dictionary and updates the corresponding attribute
            of the model if the parameter is provided. This allows for flexible reconfiguration of the model without needing to create a new instance.
            
        :param config: A dictionary containing the configuration parameters to be updated. Possible keys include "window_size", "difference_order", "data_path", and "target_column".
        :type config: dict
        
        :param window_size: The size of the input sequences to be created for the model. If provided in the config, it will update the model's window_size attribute.
        :type window_size: int
        
        :param difference_order: The order of differencing to be applied to the time series data. If provided in the config, it will update the model's difference_order attribute.
        :type difference_order: int
        
        :param data_path: The file path to the CSV file containing the time series data. If provided in the config, it will update the model's data_path attribute.
        :type data_path: str
        
        :param target_column: The name of the column in the CSV file that contains the target values for prediction. If provided in the config, it will update the model's target_column attribute.
        :type target_column: str
        
        :return: None
        :rtype: None
        """
        self.window_size = config.get("window_size", self.window_size)
        self.difference_order = config.get("difference_order", config.get("num_differences", self.difference_order))
        self.data_path = config.get("data_path", self.data_path)
        self.target_column = config.get("target_column", self.target_column)

# ——————————————————————————————————————————————————————————————
# LSTM with Attention Predictor for Time Series
class LSTMAttentionPredictor(TimeSeriesPredictor):
    def __init__(
        self,
        lstm_units: int = 64,
        window_size: int = 30,
        difference_order: int = 1,
        data_path: str = None,
        target_column: str = None,
    ):
        if difference_order < 1:
            raise ValueError("difference_order must be at least 1")

        self.window_size = window_size
        self.data_path = data_path
        self.target_column = target_column
        self.lstm_units = lstm_units
        self.difference_order = difference_order
        self.scaler = StandardScaler()

        inputs = tf.keras.layers.Input(shape=(window_size, 1))
        lstm_out = tf.keras.layers.LSTM(lstm_units, return_sequences=True)(inputs)
        attention_out = tf.keras.layers.Attention()([lstm_out, lstm_out])
        pooled = tf.keras.layers.GlobalAveragePooling1D()(attention_out)
        outputs = tf.keras.layers.Dense(1)(pooled)

        self.model = tf.keras.Model(inputs=inputs, outputs=outputs)
        self.model.compile(optimizer='adam', loss='mse', metrics=['mae'])

    def _inverse_difference(self, base_values, difference_value):
        base_values = np.asarray(base_values, dtype=float).reshape(-1)
        if base_values.size < self.difference_order:
            raise ValueError(
                f"Expected at least {self.difference_order} base values, got {base_values.size}"
            )

        current_value = float(np.asarray(difference_value, dtype=float).reshape(-1)[0])
        for order in range(self.difference_order - 1, -1, -1):
            if order == 0:
                current_value = base_values[-1] + current_value
            else:
                current_value = np.diff(base_values, n=order)[-1] + current_value

        return current_value

    def load_data(
        self,
        data_path: str,
        target_column: str,
    ):
        """Load raw values and their nth difference from a CSV file."""
        if data_path.endswith('.csv'):
            df = pd.read_csv(data_path)
            target_data = df[target_column].values.astype(float)
            target_data_diff = np.diff(target_data, n=self.difference_order)
            target_data_diff_scaled = self.scaler.fit_transform(target_data_diff.reshape(-1, 1)).flatten()
            return target_data, target_data_diff_scaled
        raise ValueError("Unsupported file format. Please provide a CSV file.")

    def prepare_data(
        self,
        data_diff: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        X, y = [], []
        for idx in range(self.window_size, len(data_diff)):
            X.append(data_diff[idx - self.window_size:idx].reshape(self.window_size, 1))
            y.append(data_diff[idx])
        return np.array(X), np.array(y)

    def split_data(
        self,
        X: np.ndarray,
        y: np.ndarray,
        train_ratio: float = 0.8
    ) -> Tuple[Tuple[np.ndarray, np.ndarray], Tuple[np.ndarray, np.ndarray]]:
        total_samples = len(X)
        train_samples = int(total_samples * train_ratio)

        X_train = X[:train_samples]
        y_train = y[:train_samples]

        X_test = X[train_samples:]
        y_test = y[train_samples:]

        return (X_train, y_train), (X_test, y_test)

    def fit(
        self,
        X_train,
        y_train,
        X_test,
        y_test,
        epochs: int = 50,
        batch_size: int = 32,
        verbose: int = 1
    ) -> None:
        self.model.fit(
            X_train,
            y_train,
            epochs=epochs,
            batch_size=batch_size,
            validation_data=(X_test, y_test),
            verbose=verbose,
            callbacks=[
                tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True, verbose=verbose),
            ]
        )

    def eval(
        self,
        X_test,
        y_test
    ) -> Tuple[float, float]:
        y_pred = self.model.predict(X_test, verbose=0).flatten()

        y_pred_unscaled = self.scaler.inverse_transform(y_pred.reshape(-1, 1)).flatten()
        y_test_unscaled = self.scaler.inverse_transform(y_test.reshape(-1, 1)).flatten()

        mse = mean_squared_error(y_test_unscaled, y_pred_unscaled)
        mae = mean_absolute_error(y_test_unscaled, y_pred_unscaled)

        print(f"Test Diff MSE: {mse:.4f}, Test Diff MAE: {mae:.4f}")

        return mse, mae

    def __call__(
        self
    ) -> None:
        data, data_diff_scaled = self.load_data(self.data_path, self.target_column)
        X, y = self.prepare_data(data_diff_scaled)
        (X_train, y_train), (X_test, y_test) = self.split_data(X, y)
        self.fit(X_train, y_train, X_test, y_test)

    def predict(
        self,
        X: Optional[np.ndarray] = None,
        n_steps: int = 1
    ):
        if X is None:
            X = self.load_data(self.data_path, self.target_column)[0]

        output = []
        raw_input = np.asarray(X, dtype=float).copy()
        required_length = self.window_size + self.difference_order

        for _ in range(n_steps):
            current_window = raw_input[-required_length:]
            if len(current_window) < required_length:
                padding = np.full((required_length - len(current_window),), current_window[-1])
                current_window = np.concatenate([current_window, padding])

            current_diff = np.diff(current_window, n=self.difference_order)
            current_diff_scaled = self.scaler.transform(current_diff.reshape(-1, 1)).reshape(1, self.window_size, 1)

            y_pred_diff_scaled = self.model.predict(current_diff_scaled, verbose=0).flatten()[0]
            y_pred_diff = self.scaler.inverse_transform(np.array([[y_pred_diff_scaled]])).flatten()[0]

            y_pred = self._inverse_difference(current_window[-self.difference_order:], y_pred_diff)
            output.append(y_pred)

            raw_input = np.append(raw_input, y_pred)

        return output

    def save_model(self, file_path: str):
        self.model.save(file_path)

    def load_model(self, file_path: str):
        self.model = tf.keras.models.load_model(file_path)

    def summary(self):
        self.model.summary()

    def get_config(self):
        return {
            "window_size": self.window_size,
            "data_path": self.data_path,
            "target_column": self.target_column,
            "difference_order": self.difference_order,
        }

    def set_config(self, config):
        self.window_size = config.get("window_size", self.window_size)
        self.data_path = config.get("data_path", self.data_path)
        self.target_column = config.get("target_column", self.target_column)
        self.difference_order = config.get("difference_order", self.difference_order)
        
class CNNPredictor(TimeSeriesPredictor):
    pass