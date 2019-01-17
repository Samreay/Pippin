import os

import pandas as pd
import numpy as np
from pippin.classifiers.classifier import Classifier


class ToyClassifier(Classifier):
    def __init__(self, light_curve_dir, fit_dir, output_dir, options):
        super().__init__(light_curve_dir, fit_dir, output_dir, options)

    def classify(self):
        os.makedirs(self.output_dir, exist_ok=True)
        fitres = f"{self.fit_dir}/FITOPT000.FITRES.gz"
        self.logger.debug(f"Looking for {fitres}")
        if not os.path.exists(fitres):
            self.logger.error(f"FITRES file could not be found at {fitres}, classifer has nothing to work with")
            return False

        data = pd.read_csv(fitres, sep='\s+', skiprows=12, comment="#", compression="infer")
        ids = data["CID"].values

        probability = np.random.uniform(size=ids.size)

        combined = np.vstack((ids, probability)).T
        print(combined.shape)

        output_file = self.output_dir + "/prob.txt"
        np.savetxt(output_file, combined)
        return True # change to hash