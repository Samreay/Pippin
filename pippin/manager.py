import os
import inspect
import subprocess
import time

from pippin.classifiers.classifier import Classifier
from pippin.classifiers.factory import ClassifierFactory
from pippin.config import get_logger, get_config
from pippin.snana_fit import SNANALightCurveFit
from pippin.snana_simulation import SNANASimulation
from pippin.task import Task


class Manager:
    def __init__(self, filename, config):
        self.logger = get_logger()
        self.filename = filename
        self.run_config = config
        self.global_config = get_config()

        self.prefix = self.global_config["GLOBAL"]["prefix"] + "_" + filename
        self.max_jobs = int(self.global_config["GLOBAL"]["max_jobs"])
        self.max_jobs_in_queue = int(self.global_config["GLOBAL"]["max_jobs_in_queue"])

        self.output_dir = os.path.abspath(os.path.dirname(inspect.stack()[0][1]) + "/../" + self.global_config['OUTPUT']['output_dir'] + "/" + self.filename)
        self.tasks = None

    def get_tasks(self, config):
        sim_tasks = self.get_simulation_tasks(config)
        lcfit_tasks = self.get_lcfit_tasks(config, sim_tasks)
        classification_tasks = self.get_classification_tasks(config, sim_tasks, lcfit_tasks)
        total_tasks = sim_tasks + lcfit_tasks + classification_tasks
        self.logger.info("")
        self.logger.info("Listing tasks:")
        for task in total_tasks:
            self.logger.info(str(task))
        self.logger.info("")
        return total_tasks

    def get_simulation_tasks(self, c):
        tasks = []
        for sim_name in c.get("SIM", []):
            sim_output_dir = self._get_sim_output_dir(sim_name)
            s = SNANASimulation(sim_name, sim_output_dir, f"{self.prefix}_{sim_name}", c["SIM"][sim_name], self.global_config)
            self.logger.debug(f"Creating simulation task {sim_name} with {s.num_jobs} jobs, output to {sim_output_dir}")
            tasks.append(s)
        return tasks

    def get_lcfit_tasks(self, c, sim_tasks):
        tasks = []
        for fit_name in c.get("LCFIT", []):
            fit_config = c["LCFIT"][fit_name]
            for sim in sim_tasks:
                if fit_config.get("MASK") is None or fit_config.get("MASK") in sim.name:
                    fit_output_dir = self._get_lc_output_dir(sim.name, fit_name)
                    f = SNANALightCurveFit(fit_name, fit_output_dir, f"{self.prefix}_{sim.name}", fit_config, self.global_config)
                    self.logger.info(f"Creating fitting task {fit_name} with {f.num_jobs} jobs, for simulation {sim.name}")
                    f.add_dependency(sim)
                    tasks.append(f)
        return tasks

    def get_classification_tasks(self, c, sim_tasks, lcfit_tasks):
        tasks = []

        for clas_name in c.get("CLASSIFICATION", []):
            config = c["CLASSIFICATION"][clas_name]
            name = config["CLASSIFIER"]
            cls = ClassifierFactory.get(name)
            options = config.get("OPTS", {})
            mode = config["MODE"].lower()
            assert mode in ["train", "predict"], "MODE should be either train or predict"
            if mode == "train":
                mode = Classifier.TRAIN
            else:
                mode = Classifier.PREDICT

            needs_sim, needs_lc = cls.get_requirements(options)
            runs = []
            if needs_sim and needs_lc:
                runs = [(l.dependencies[0], l) for l in lcfit_tasks]
            elif needs_sim:
                runs = [(s, None) for s in sim_tasks]
            elif needs_lc:
                runs = [(None, l) for l in lcfit_tasks]

            for s, l in runs:
                sim_name = s.name if s is not None else None
                fit_name = l.name if l is not None else None
                mask = config.get("MASK", "")
                if sim_name is not None and mask not in sim_name:
                    continue
                if fit_name is not None and mask not in fit_name:
                    continue
                clas_output_dir = self._get_clas_output_dir(sim_name, fit_name, clas_name)
                cc = cls(clas_name, self._get_phot_output_dir(sim_name), self._get_lc_output_dir(sim_name, fit_name) + f"/output/{self.prefix}_{sim_name}", clas_output_dir, mode, options)
                self.logger.info(f"Creating classification task {clas_name} with {cc.num_jobs} jobs, for LC fit {fit_name} on simulation {sim_name}")
                if s is not None:
                    cc.add_dependency(s)
                if l is not None:
                    cc.add_dependency(l)

                model = options.get("MODEL")
                if model is not None:
                    for t in tasks:
                        if model == t.name:
                            cc.add_dependency(t)
                tasks.append(cc)
        return tasks

    def get_num_running_jobs(self):
        num_jobs = int(subprocess.check_output("squeue -ho %A -u $USER | wc -l", shell=True, stderr=subprocess.STDOUT))
        return num_jobs

    def get_task_index_to_run(self, tasks_to_run, done_tasks):
        for t in tasks_to_run:
            can_run = True
            for dep in t.dependencies:
                if dep not in done_tasks:
                    can_run = False
            if can_run:
                return t
        return None

    def execute(self):
        self.logger.info(f"Executing pipeline for prefix {self.prefix}")
        self.logger.info(f"Output will be located in {self.output_dir}")
        c = self.run_config

        self.tasks = self.get_tasks(c)
        running_tasks = []
        done_tasks = []
        failed_tasks = []
        blocked_tasks = []

        while self.tasks or running_tasks:

            # Check status of current jobs
            for t in running_tasks:
                result = t.check_completion
                if result in [Task.FINISHED_GOOD, Task.FINISHED_CRASH]:
                    running_tasks.remove(t)
                    if result == Task.FINISHED_GOOD:
                        self.logger.info("Task {t} finished successfully")
                        done_tasks.append(t)
                    else:
                        failed_tasks.append(t)
                        self.logger.error("Task {t} crashed")
                        if os.path.exists(t.hash_file):
                            os.remove(t.hash_file)
                        for t2 in self.tasks:
                            if t in t2.dependencies:
                                self.tasks.remove(t2)
                                blocked_tasks.append(t2)

            # Submit new jobs if needed
            num_running = self.get_num_running_jobs()
            while num_running < self.max_jobs:
                t = self.get_task_index_to_run(self.tasks, done_tasks)
                if t is not None:
                    self.tasks.remove(t)
                    running_tasks.append(t)
                    num_running += t.num_jobs
                    t.run()
                else:
                    break

            time.sleep(self.global_config["OUTPUT"].getint("ping_frequency"))

    def _get_sim_output_dir(self, sim_name):
        return f"{self.output_dir}/0_SIM/{self.prefix}_{sim_name}"

    def _get_phot_output_dir(self, sim_name):
        return f"{self.output_dir}/0_SIM/{self.prefix}_{sim_name}/{self.prefix}_{sim_name}"

    def _get_lc_output_dir(self, sim_name, fit_name):
        return f"{self.output_dir}/1_LCFIT/{self.prefix}_{sim_name}_{fit_name}"

    def _get_clas_output_dir(self, sim_name, fit_name, clas_name):
        return f"{self.output_dir}/2_CLAS/{self.prefix}_{sim_name}_{fit_name}_{clas_name}"

if __name__ == "__main__":
    import logging
    import yaml
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(levelname)8s |%(filename)20s:%(lineno)3d |%(funcName)25s]   %(message)s")

    with open("../configs/test.yml", "r") as f:
        config = yaml.safe_load(f)

    manager = Manager("test", config)
    manager.execute()
