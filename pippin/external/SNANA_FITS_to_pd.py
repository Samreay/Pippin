import numpy as np
import pandas as pd
from pathlib import Path
from astropy.table import Table
import os

"""
    SNANA simulation/data format to pandas
"""

def read_fits(fname,drop_separators=False):
    """Load SNANA formatted data and cast it to a PANDAS dataframe

    Args:
        fname (str): path + name to PHOT.FITS file
        drop_separators (Boolean): if -777 are to be dropped

    Returns:
        (pandas.DataFrame) dataframe from PHOT.FITS file (with ID)
        (pandas.DataFrame) dataframe from HEAD.FITS file
    """

    # load photometry
    dat = Table.read(fname, format='fits')
    df_phot = dat.to_pandas()
    # failsafe
    if df_phot.MJD.values[-1] == -777.0:
        df_phot = df_phot.drop(df_phot.index[-1])
    if df_phot.MJD.values[0] == -777.0:
        df_phot = df_phot.drop(df_phot.index[0])

    # load header
    header = Table.read(fname.replace("PHOT", "HEAD"), format="fits")
    df_header = header.to_pandas()
    df_header["SNID"] = df_header["SNID"].astype(np.int32)

    # add SNID to phot for skimming
    arr_ID = np.zeros(len(df_phot), dtype=np.int32)
    # New light curves are identified by MJD == -777.0
    arr_idx = np.where(df_phot["MJD"].values == -777.0)[0]
    arr_idx = np.hstack((np.array([0]), arr_idx, np.array([len(df_phot)])))
    # Fill in arr_ID
    for counter in range(1, len(arr_idx)):
        start, end = arr_idx[counter - 1], arr_idx[counter]
        # index starts at zero
        arr_ID[start:end] = df_header.SNID.iloc[counter - 1]
    df_phot["SNID"] = arr_ID

    if drop_separators:
        df_phot = df_phot[df_phot.MJD != -777.000]

    df_header = df_header[["SNID", "SNTYPE", "PEAKMJD", "REDSHIFT_FINAL", "MWEBV"]]
    df_phot = df_phot[["SNID", "MJD", "FLT", "FLUXCAL", "FLUXCALERR"]]
    df_header = df_header.rename(columns={"SNID":"object_id", "SNTYPE": "true_target", "PEAKMJD": "true_peakmjd", "REDSHIFT_FINAL": "true_z", "MWEBV": "mwebv"})
    df_header.replace({"true_target": 
        {120: 42, 20: 42, 121: 42, 21: 42, 122: 42, 22: 42, 130: 62, 30: 62, 131: 62, 31: 62, 101: 90, 1: 90, 102: 52, 2: 52, 104: 64, 4: 64, 103: 95, 3: 95, 191: 67, 91: 67}}, inplace=True)
    print(df_header)
    df_phot = df_phot.rename(columns={"SNID":"object_id", "MJD": "mjd", "FLT": "passband", "FLUXCAL": "flux", "FLUXCALERR": "flux_err"})
    df_phot.replace({"passband":
        {b'u ': 0, b'g ': 1, b'r ': 2, b'i ': 3, b'z ': 4, b'Y ': 5}}, inplace=True)

    return df_header, df_phot

def save_fits(df, fname):
    """Save data frame in fits table

    Arguments:
        df {pandas.DataFrame} -- data to save
        fname {str} -- outname, must end in .FITS
    """

    keep_cols = df.keys()
    df = df.reset_index()
    df = df[keep_cols]

    outtable = Table.from_pandas(df)
    Path(fname).parent.mkdir(parents=True, exist_ok=True)
    outtable.write(fname, format='fits', overwrite=True)