import shutil
import subprocess
import os
from pathlib import Path

from pippin.config import mkdirs, get_config, get_output_loc, get_data_loc
from pippin.create_cov import CreateCov
from pippin.task import Task


class CosmoMC(Task):  # TODO: Define the location of the output so we can run the lc fitting on it.
    """ Run cosmomc given an ini file

    CONFIGURATION
    =============

    COSMOMC:
        label:
            MASK_CREATE_COV: mask  # partial match
            OPTS:  # Options
                INI: sn_cmb_omw  # should match the filename of at a file in the pippin/data_files/cosmomc_templates directory
                COVOPTS: [ALL, NOSYS]  # Optional, covopts from CREATE_COV step to run against. If you leave this blank, you get them all. Exact matching.
                NUM_WALKERS: 8  # Optional, defaults to eight.

    OUTPUTS
    =======

    chain_dir : directory where cosmomc chains will be located
    num_walkers : number of output chains
    covopts : list of covopts
    labels : a list of the names generated by combining task name with covopt name. eg SN_CMB_OMW_ALL or SN_CMB_OMW_NOSYS
    base_dict : a map from covopt label to a base files which you can add .params to
    chain_dict : a map from covopt label to a .txt file to be loaded in
    param_dict : a map from covopt label to a text file listing param names and latex label
    cosmology_params : a list of cosmology parameter names output by cosmomc that we really care about
    blind: bool - whether or not to blind cosmo results

    """

    def __init__(self, name, output_dir, options, global_config, dependencies=None):
        self.data_dirs = global_config["DATA_DIRS"]
        super().__init__(name, output_dir, dependencies=dependencies)
        self.options = options
        self.global_config = global_config

        self.job_name = os.path.basename(Path(output_dir).parents[1]) + "_COSMOMC_" + name
        self.logfile = os.path.join(self.output_dir, "output.log")

        self.path_to_cosmomc = get_output_loc(self.global_config["CosmoMC"]["location"])

        self.create_cov_dep = self.get_dep(CreateCov)
        self.blind = self.create_cov_dep.output["blind"] if self.create_cov_dep is not None else self.options.get("BLIND", False)
        assert isinstance(self.blind, bool), "Blind should be set to a boolan value!"
        self.ini_prefix = options.get("INI")
        self.static = self.ini_prefix in ["cmb_omw", "cmb_omol"]
        self.static_path = "cosmomc_static_chains/"

        if self.create_cov_dep is None:
            self.ini_files = [f"{self.ini_prefix}.ini"]
            self.num_walkers = 4
            self.covopts = ["ALL"]
            self.covopts_numbers = [0]
            self.labels = [self.name]
            self.num_jobs = 1
        else:
            self.num_walkers = options.get("NUM_WALKERS", 8)
            avail_cov_opts = self.create_cov_dep.output["covopts"]
            self.covopts = options.get("COVOPTS") or list(avail_cov_opts.keys())
            self.covopts_numbers = [avail_cov_opts[k] for k in self.covopts]

            self.ini_files = [f"{self.ini_prefix}_{num}.ini" for num in self.covopts_numbers]

            self.output["hubble_plot"] = self.create_cov_dep.output["hubble_plot"]
            self.output["bcor_name"] = self.create_cov_dep.output["bcor_name"]
            self.labels = [self.name + "_" + c for c in self.covopts]
            self.num_jobs = len(self.covopts) * self.num_walkers

        self.chain_dir = os.path.join(self.output_dir, "chains/")
        self.param_dict = {l: os.path.join(self.chain_dir, i.replace(".ini", ".paramnames")) for l, i in zip(self.covopts, self.ini_files)}

        self.done_files = [f"done_{num}.txt" for num in self.covopts_numbers]
        self.chain_dict = {
            l: os.path.join(self.chain_dir, i.replace(".ini", f"_{n + 1}.txt")) for l, i in zip(self.covopts, self.ini_files) for n in range(self.num_walkers)
        }
        self.base_dict = {l: os.path.join(self.chain_dir, i.replace(".ini", "")) for l, i in zip(self.covopts, self.ini_files) for n in range(self.num_walkers)}
        self.output["chain_dir"] = self.chain_dir
        self.output["param_dict"] = self.param_dict
        self.output["chain_dict"] = self.chain_dict
        self.output["base_dict"] = self.base_dict
        self.output["covopts"] = self.covopts
        self.output["blind"] = self.blind

        self.output["label"] = (
            self.options.get("LABEL", f"({' + '.join(self.ini_prefix.upper().split('_')[:-1])})")
            + " "
            + (self.create_cov_dep.output["name"] if self.create_cov_dep is not None else "")
        )
        final = self.ini_prefix.split("_")[-1]
        ps = {"omw": ["omegam", "w"], "omol": ["omegam", "omegal"], "wnu": ["w", "nu"], "wwa": ["w", "wa"]}
        self.output["cosmology_params"] = ps[final]

        self.slurm = """#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --time=34:00:00
###SBATCH --nodes=1
#SBATCH --ntasks={num_walkers}
#SBATCH --array=1-{num_jobs}
#SBATCH --cpus-per-task=1
#SBATCH --partition=broadwl
#SBATCH --output={log_file}
#SBATCH --account=pi-rkessler
#SBATCH --mem=20GB

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK

module unload openmpi
module load intelmpi/5.1+intel-16.0
module load cfitsio/3
module load mkl

PARAMS=`expr ${{SLURM_ARRAY_TASK_ID}} - 1`

INI_FILES=({ini_files})
DONE_FILES=({done_files})

cd {output_dir}
mpirun {path_to_cosmomc}/cosmomc ${{INI_FILES[$PARAMS]}}

if [ $? -eq 0 ]; then
    echo "SUCCESS" > ${{DONE_FILES[$PARAMS]}}
else
    echo "FAILURE" > ${{DONE_FILES[$PARAMS]}}
fi
"""

    def _check_completion(self, squeue):
        if os.path.exists(self.done_file):
            self.logger.debug(f"Done file found at f{self.done_file}")
            with open(self.done_file) as f:
                if "FAILURE" in f.read():
                    self.logger.error(f"Done file reported failure. Check output log {self.logfile}")
                    return Task.FINISHED_FAILURE
                else:
                    return Task.FINISHED_SUCCESS
        else:
            all_files = True
            for d in self.done_files:
                df = os.path.join(self.output_dir, d)
                if os.path.exists(df):
                    with open(df) as f:
                        if "FAILURE" in f.read():
                            self.logger.error(f"Done file {d} reported failure. Check output log {self.logfile}")
                            return Task.FINISHED_FAILURE
                else:
                    all_files = False
            if all_files:
                # Check that the expected outputs exist to
                for file in self.chain_dict.values():
                    if not os.path.exists(file):
                        self.logger.error(f"No chain found at {file}")
                        return Task.FINISHED_FAILURE
                for file in self.param_dict.values():
                    if not os.path.exists(file):
                        self.logger.error(f"No paramnames file at {file}")
                        return Task.FINISHED_FAILURE
                with open(self.done_file, "w") as f:
                    f.write("SUCCESS")
                return Task.FINISHED_SUCCESS
            else:
                if os.path.exists(self.logfile):
                    with open(self.logfile) as f:
                        if "CANCELLED AT" in f.read():
                            self.logger.debug(f"The job was cancelled! Check {self.logfile} for details")
                            return Task.FINISHED_FAILURE
        return self.check_for_job(squeue, self.job_name)

    def get_ini_file(self):
        mkdirs(self.chain_dir)
        directory = self.create_cov_dep.output["ini_dir"]

        input_files = []
        for file in self.ini_files:
            path = os.path.join(directory, file)
            if not os.path.exists(path):
                self.logger.error(f"Cannot find the file {path}, make sure you specified a correct INI string matching an existing template")
                return None
            self.logger.debug(f"Reading in {path} to format")
            with open(path) as f:
                input_files.append(
                    f.read().format(**{"path_to_cosmomc": self.path_to_cosmomc, "ini_dir": self.create_cov_dep.output["ini_dir"], "root_dir": self.chain_dir})
                )

        return input_files

    def _run(self, force_refresh):

        if self.static:
            self.logger.info("CMB only constraints detected, copying static files")

            cosmomc_static_loc = get_data_loc(self.data_dirs, self.static_path + self.ini_prefix)
            if cosmomc_static_loc is None:
                self.logger.error("Seems like we can't find the static chains...")
                return False
            else:

                new_hash = self.get_hash_from_string(cosmomc_static_loc)
                old_hash = self.get_old_hash()

                if force_refresh or new_hash != old_hash:
                    self.logger.debug("Regenerating and copying static chains")
                    mkdirs(self.chain_dir)
                    shutil.copy(cosmomc_static_loc, self.chain_dir)
                else:
                    self.should_be_done()
                    self.logger.info("Hash check passed, not rerunning")
        else:
            ini_filecontents = self.get_ini_file()
            if ini_filecontents is None:
                return False

            format_dict = {
                "job_name": self.job_name,
                "log_file": self.logfile,
                "done_files": " ".join(self.done_files),
                "path_to_cosmomc": self.path_to_cosmomc,
                "output_dir": self.output_dir,
                "ini_files": " ".join(self.ini_files),
                "num_jobs": len(self.ini_files),
                "num_walkers": self.num_walkers,
            }
            final_slurm = self.slurm.format(**format_dict)

            new_hash = self.get_hash_from_string(final_slurm + " ".join(ini_filecontents))
            old_hash = self.get_old_hash()

            if force_refresh or new_hash != old_hash:
                self.logger.debug("Regenerating and launching task")
                shutil.rmtree(self.output_dir, ignore_errors=True)
                mkdirs(self.output_dir)
                self.save_new_hash(new_hash)
                slurm_output_file = os.path.join(self.output_dir, "slurm.job")
                with open(slurm_output_file, "w") as f:
                    f.write(final_slurm)
                for file, content in zip(self.ini_files, ini_filecontents):
                    filepath = os.path.join(self.output_dir, file)
                    with open(filepath, "w") as f:
                        f.write(content)
                mkdirs(self.chain_dir)

                needed_dirs = ["data", "paramnames", "camb", "batch1", "batch2", "batch3"]
                for d in needed_dirs:
                    self.logger.debug(f"Creating symlink to {d} dir")
                    original_data_dir = os.path.join(self.path_to_cosmomc, d)
                    new_data_dir = os.path.join(self.output_dir, d)
                    os.symlink(original_data_dir, new_data_dir, target_is_directory=True)

                self.logger.info(f"Submitting batch job for data prep")
                subprocess.run(["sbatch", slurm_output_file], cwd=self.output_dir)
            else:
                self.should_be_done()
                self.logger.info("Hash check passed, not rerunning")
        return True

    @staticmethod
    def get_tasks(c, prior_tasks, base_output_dir, stage_number, prefix, global_config):
        create_cov_tasks = Task.get_task_of_type(prior_tasks, CreateCov)

        def _get_cosmomc_dir(base_output_dir, stage_number, name):
            return f"{base_output_dir}/{stage_number}_COSMOMC/{name}"

        tasks = []
        key = "COSMOMC"
        for cname in c.get(key, []):
            config = c[key].get(cname, {})
            options = config.get("OPTS", {})

            mask = config.get("MASK_CREATE_COV", "")

            # Check if this is static. Could scan the folder, but dont have all the chains yet.
            # TODO: Update this when I have all the chains
            if options.get("INI") in ["cmb_omw", "cmb_omol"]:
                a = CosmoMC(cname, _get_cosmomc_dir(base_output_dir, stage_number, cname), options, global_config)
                Task.logger.info(f"Creating CosmoMC task {cname} for {a.num_jobs} jobs")
                tasks.append(a)

            else:
                for ctask in create_cov_tasks:
                    if mask not in ctask.name:
                        continue
                    name = f"{cname}_{ctask.name}"
                    a = CosmoMC(name, _get_cosmomc_dir(base_output_dir, stage_number, name), options, global_config, dependencies=[ctask])
                    Task.logger.info(f"Creating CosmoMC task {name} for {ctask.name} with {a.num_jobs} jobs")
                    tasks.append(a)

                if len(create_cov_tasks) == 0:
                    Task.fail_config(f"CosmoMC task {cname} has no create_cov task to run on!")

        return tasks
