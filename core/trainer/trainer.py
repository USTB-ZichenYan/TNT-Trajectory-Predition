# trainner to train the models

import os
from typing import Dict

import json

import torch

# from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
from torch_geometric.data import DataLoader
from argoverse.evaluation.eval_forecasting import get_displacement_errors_and_miss_rate


class Trainer(object):
    """
    Parent class for all the trainer class
    """
    def __init__(self,
                 train_loader: DataLoader,
                 eval_loader: DataLoader,
                 test_loader: DataLoader = None,
                 batch_size: int = 1,
                 lr: float = 1e-4,
                 betas=(0.9, 0.999),
                 weight_decay: float = 0.01,
                 warmup_epoch=5,
                 with_cuda: bool = False,
                 cuda_device=None,
                 log_freq: int = 2,
                 save_folder: str = "",
                 verbose: bool = True
                 ):
        """
        :param train_loader: train dataset dataloader
        :param eval_loader: eval dataset dataloader
        :param test_loader: dataset dataloader
        :param lr: initial learning rate
        :param betas: Adam optiimzer betas
        :param weight_decay: Adam optimizer weight decay param
        :param warmup_steps: optimizatioin scheduler param
        :param with_cuda: tag indicating whether using gpu for training
        :param multi_gpu: tag indicating whether multiple gpus are using
        :param log_freq: logging frequency in epoch
        :param verbose: whether printing debug messages
        """
        # determine cuda device id
        self.cuda_id = cuda_device if with_cuda and cuda_device else 0
        self.device = torch.device("cuda:{}".format(self.cuda_id) if torch.cuda.is_available() and with_cuda else "cpu")

        # dataset
        self.trainset = train_loader
        self.evalset = eval_loader
        self.testset = test_loader
        self.batch_size = batch_size

        # model
        self.model = None
        self.multi_gpu = False

        # optimizer params
        self.lr = lr
        self.betas = betas
        self.weight_decay = weight_decay
        self.warmup_epoch = warmup_epoch
        self.optim = None
        self.optm_schedule = None

        # criterion and metric
        self.criterion = None
        self.min_eval_loss = None

        # log
        self.save_folder = save_folder
        self.logger = SummaryWriter(log_dir=os.path.join(self.save_folder, "log"))
        self.log_freq = log_freq
        self.verbose = verbose

    def train(self, epoch):
        raise NotImplementedError

    def eval(self, epoch):
        raise NotImplementedError

    def test(self, data):
        raise NotImplementedError

    def iteration(self, epoch, dataloader):
        raise NotImplementedError

    def write_log(self, name_str, data, epoch):
        self.logger.add_scalar(name_str, data, epoch)

    # todo: save the model and current training status
    def save(self, iter_epoch, loss):
        """
        save current state of the training and update the minimum loss value
        :param save_folder: str, the destination folder to store the ckpt
        :param iter_epoch: int, ith epoch of current saving checkpoint
        :param loss: float, the loss of current saving state
        :return:
        """
        self.min_eval_loss = loss
        if not os.path.exists(self.save_folder):
            os.makedirs(self.save_folder, exist_ok=True)
        torch.save({
            "epoch": iter_epoch,
            "model_state_dict": self.model.state_dict() if not self.multi_gpu else self.model.module.state_dict(),
            "optimizer_state_dict": self.optim.state_dict(),
            "min_eval_loss": loss
        }, os.path.join(self.save_folder, "checkpoint_iter{}.ckpt".format(iter_epoch)))
        if self.verbose:
            print("[Trainer]: Saving checkpoint to {}...".format(self.save_folder))

    def save_model(self, prefix=""):
        """
        save current state of the model
        :param save_folder: str, the folder to store the model file
        :return:
        """
        if not os.path.exists(self.save_folder):
            os.makedirs(self.save_folder, exist_ok=True)
        torch.save(
            self.model.state_dict() if not self.multi_gpu else self.model.module.state_dict(),
            os.path.join(self.save_folder, "{}_{}.pth".format(prefix, type(self.model).__name__))
        )
        if self.verbose:
            print("[Trainer]: Saving model to {}...".format(self.save_folder))

        # compute the metrics and save
        _ = self.compute_metric(stored_file=os.path.join(self.save_folder, "{}_metrics.txt".format(prefix)))

    def load(self, load_path, mode='c'):
        """
        loading function to load the ckpt or model
        :param mode: str, "c" for checkpoint, or "m" for model
        :param load_path: str, the path of the file to be load
        :return:
        """
        if mode == 'c':
            # load ckpt
            ckpt = torch.load(load_path)
            try:
                self.model.load_state_dict(ckpt["model_state_dict"])
                self.optim.load_state_dict(ckpt["optimizer_state_dict"])
                self.min_eval_loss = ckpt["min_eval_loss"]
            except:
                raise Exception("[Trainer]: Error in loading the checkpoint file {}".format(load_path))
        elif mode == 'm':
            try:
                self.model.load_state_dict(torch.load(load_path))
            except:
                raise Exception("[Trainer]: Error in loading the model file {}".format(load_path))
        else:
            raise NotImplementedError

    def compute_metric(self, miss_threshold=2.0, stored_file=None):
        """
        compute metric for test dataset
        :param miss_threshold: float,
        :param stored_file: str, store the result metric in the file
        :return:
        """
        assert self.model, "[Trainer]: No valid model, metrics can't be computed!"
        assert self.testset, "[Trainer]: No test dataset, metrics can't be computed!"

        forecasted_trajectories, gt_trajectories = {}, {}
        seq_id = 0
        self.model.eval()
        with torch.no_grad():
            for data in self.testset:
                gt = data.y.view(-1, 2).cumsum(axis=0).numpy()

                # inference and transform dimension
                out = self.model(data.to(self.device))
                pred_y = out.view((-1, 2)).cumsum(axis=0).cpu().numpy()

                # record the prediction and ground truth
                forecasted_trajectories[seq_id] = [pred_y]
                gt_trajectories[seq_id] = gt
                seq_id += 1

            metric_results = get_displacement_errors_and_miss_rate(
                forecasted_trajectories,
                gt_trajectories,
                self.model.k,
                self.model.horizon,
                miss_threshold
            )
        if stored_file:
            with open(stored_file, 'w+') as f:
                assert isinstance(metric_results, dict), "[Trainer] The metric evaluation result is not valid!"
                f.write(json.dumps(metric_results))
        return metric_results
