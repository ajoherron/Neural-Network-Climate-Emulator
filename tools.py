import numpy as np
import xarray as xr
import torch
import torch.nn as nn
import torch.optim as optim
from tqdm import tqdm
from sklearn.model_selection import KFold
from sklearn.metrics import mean_squared_error as mse
from scipy.stats import distributions

from data import normalize, input_for_training


def train_single_model(model, criterion, optimizer, X_train_k, Y_train_k, epochs):
    # def train_model(model, criterion, optimizer, X_train_k, Y_train_k, epochs):
    train_losses = []
    for epoch in tqdm(range(epochs)):
        model.train()
        optimizer.zero_grad()
        X_train_k_tensor = torch.tensor(X_train_k, dtype=torch.float32).permute(
            0, 3, 1, 2
        )
        outputs = model(X_train_k_tensor)
        Y_train_k_tensor = torch.tensor(Y_train_k, dtype=torch.float32).view(
            -1, 90, 144, 1
        )
        loss = criterion(outputs, Y_train_k_tensor)
        loss.backward()
        optimizer.step()
        train_losses.append(loss.item())
    return train_losses


def format_predictions(m_pred_tensor, VARIABLE, X_test_xr, Y_test, SLIDER_LENGTH):
    # def process_predictions(m_pred_tensor, VARIABLE, X_test_xr, Y_test, SLIDER_LENGTH):
    m_pred = xr.Dataset()
    if SLIDER_LENGTH == 1:
        m_pred_data = m_pred_tensor.reshape(
            m_pred_tensor.shape[0], m_pred_tensor.shape[1], m_pred_tensor.shape[2]
        )
    else:
        m_pred_data = m_pred_tensor.reshape(
            m_pred_tensor.shape[0], m_pred_tensor.shape[2], m_pred_tensor.shape[3]
        )
    m_pred = xr.DataArray(
        m_pred_data,
        dims=["year", "lat", "lon"],
        coords=[
            X_test_xr.year.data[SLIDER_LENGTH - 1 :],
            Y_test[0].lat.data,
            X_test_xr.lon.data,
        ],
    )
    m_pred = (
        m_pred.transpose("year", "lat", "lon")
        .sel(year=slice(1850, 2100))
        .to_dataset(name=VARIABLE[0])
    )
    return m_pred


def train_model_k_fold(
    INPUT_LIST, VARIABLE, LEARNING_RATE, X_train_all, Y_train_all, EPOCHS, model
):
    # def train_k_fold(
    #    INPUT_LIST, VARIABLE, LEARNING_RATE, X_train_all, Y_train_all, EPOCHS, model
    # ):

    criterion = nn.L1Loss()
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)

    # Initialize a list to store the training losses for each fold
    all_train_losses = []

    kf = KFold(n_splits=2)
    for train_index, val_index in kf.split(np.zeros(len(Y_train_all)), Y_train_all):
        X_train_k = X_train_all[train_index]
        Y_train_k = Y_train_all[train_index]

        # Train the model and get the training losses
        train_losses = train_single_model(
            model, criterion, optimizer, X_train_k, Y_train_k, EPOCHS
        )
        all_train_losses.append(train_losses)

    return model, all_train_losses


def make_model_predictions(
    X_test, Y_test, model, INPUT_LIST, meanstd_inputs, SLIDER_LENGTH, VARIABLE
):
    # def predict_model(
    #     X_test, Y_test, model, INPUT_LIST, meanstd_inputs, SLIDER_LENGTH, VARIABLE
    # ):
    ## predict things
    X_test_norm = []
    m_pred_col = []

    # Normalize data
    for i, (test_xr, y_true) in enumerate(zip(X_test, Y_test)):
        for var in INPUT_LIST:
            var_dims = test_xr[var].dims
            test_xr = test_xr[INPUT_LIST].assign(
                {var: (var_dims, normalize(test_xr[var].data, var, meanstd_inputs))}
            )

        X_test_np = input_for_training(test_xr, slider=SLIDER_LENGTH)
        if SLIDER_LENGTH == 1:
            X_test_np = X_test_np[:, 0, :, :, :]

        # Convert to tensor and add batch dimension
        X_test_tensor = torch.tensor(X_test_np, dtype=torch.float32).permute(0, 3, 1, 2)

        # Make predictions using trained model
        model.eval()  # Set model to evaluation mode
        with torch.no_grad():
            m_pred_tensor = model(X_test_tensor)

        # Process predictions
        m_pred = format_predictions(
            m_pred_tensor, VARIABLE, test_xr, Y_test, SLIDER_LENGTH
        )

        # Append predictions to m_pred_col
        if i == 0:
            m_pred_col = m_pred
            y_true_col = y_true
        else:
            m_pred_col = xr.concat([m_pred_col, m_pred], "run")
            y_true_col = xr.concat([y_true_col, y_true], "run")

    return m_pred_col, y_true_col


def generate_all_predictions(
    X_test,
    Y_test,
    X_train,
    Y_train,
    model,
    INPUT_LIST,
    meanstd_inputs,
    SLIDER_LENGTH,
    RUN_ID_MS,
    predict_model,
    VARIABLE,
):

    # def make_predictions(
    #     X_test,
    #     Y_test,
    #     X_train,
    #     Y_train,
    #     model,
    #     INPUT_LIST,
    #     meanstd_inputs,
    #     SLIDER_LENGTH,
    #     RUN_ID_MS,
    #     predict_model,
    #     VARIABLE,
    # ):
    # Make predictions on test data
    m_pred_col, y_true_col = predict_model(
        X_test,
        Y_test,
        model,
        INPUT_LIST,
        meanstd_inputs,
        SLIDER_LENGTH,
        VARIABLE,
    )

    # Check predictions on SSP585 and SSP126
    if len(X_train) < len(RUN_ID_MS):  # ensemble averages
        m_pred_col_585, y_true_col_585 = predict_model(
            X_train[0:1],
            Y_train[0:1],
            model,
            INPUT_LIST,
            meanstd_inputs,
            SLIDER_LENGTH,
            VARIABLE,
        )
        m_pred_col_126, y_true_col_126 = predict_model(
            X_train[1:2],
            Y_train[1:2],
            model,
            INPUT_LIST,
            meanstd_inputs,
            SLIDER_LENGTH,
            VARIABLE,
        )
    else:  # non-averaged training
        m_pred_col_585, y_true_col_585 = predict_model(
            X_train[0:3],
            Y_train[0:3],
            model,
            INPUT_LIST,
            meanstd_inputs,
            SLIDER_LENGTH,
            VARIABLE,
        )
        m_pred_col_126, y_true_col_126 = predict_model(
            X_train[3:6],
            Y_train[3:6],
            model,
            INPUT_LIST,
            meanstd_inputs,
            SLIDER_LENGTH,
            VARIABLE,
        )

    return (
        m_pred_col,
        y_true_col,
        m_pred_col_585,
        y_true_col_585,
        m_pred_col_126,
        y_true_col_126,
    )


def calculate_rmse(y_pred, y_test, VARIABLE):
    # def RMSE(y_pred, y_test, VARIABLE):
    sq_diff = (y_pred[VARIABLE[0]] - y_test[VARIABLE[0]]) ** 2
    mean_sq_diff = np.mean(sq_diff)
    rmse = np.sqrt(mean_sq_diff)
    return rmse.values


def compute_evaluation_metrics(Y_hat, Y_test, VARIABLE):
    # def metrics(Y_hat, Y_test, VARIABLE):
    Y_hat_slice_avg = Y_hat[VARIABLE[0]].sel(year=slice(2080, 2100)).mean(dim="year")
    Y_test_slice_avg = Y_test[VARIABLE[0]].sel(year=slice(2080, 2100)).mean(dim="year")
    spatial_RMSE = np.sqrt(mse(Y_hat_slice_avg, Y_test_slice_avg))
    print(f"Spatial RMSE: {spatial_RMSE}")

    axyp = xr.open_dataset(
        "/discover/nobackup/jmekus/E213SSP585/annE213SSP585bF40oQ40_2015-2100/ANN2100.aijE213SSP585bF40oQ40.nc"
    ).axyp
    global_mean_pred = (Y_hat[VARIABLE[0]].sel(year=slice(2080, 2100)) * axyp).sum(
        dim={"lat", "lon"}
    ) / axyp.sum(dim={"lat", "lon"})
    global_mean_test = (Y_test[VARIABLE[0]].sel(year=slice(2080, 2100)) * axyp).sum(
        dim={"lat", "lon"}
    ) / axyp.sum(dim={"lat", "lon"})
    global_RMSE = np.sqrt(mse(global_mean_pred, global_mean_test))
    print(f"Global RMSE: {global_RMSE}")

    return spatial_RMSE, global_RMSE


def t_test(diff_mean, diff_std, diff_num):
    """
    Calculates the T-test for the means of *two independent* samples of scores.

    This is a two-sided test for the null hypothesis that 2 independent samples
    have identical average (expected) values. This test assumes that the
    populations have identical variances by default.

    It is deliberately similar in interface to the other scipy.stats.ttest_... routines

    See e.g. https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.ttest_ind_from_stats.html
    and pg. 140 in Statistical methods in Atmos Sciences

    :param diff: The mean difference, x_d (|x1 - x1| == |x1| - |x2|)
    :param diff_std: The standard deviation in the difference, s_d (sqrt(Var[x_d]))
    :param diff_num: The number of points, n (n == n1 == n2)
    :return float, float: t-statistic, p-value
    """
    z = diff_mean / np.sqrt(diff_std**2 / diff_num)
    # use np.abs to get upper tail, then multiply by two as this is a two-tailed test
    p = distributions.t.sf(np.abs(z), diff_num - 1) * 2
    return z, p
