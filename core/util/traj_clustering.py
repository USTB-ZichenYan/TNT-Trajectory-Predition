import os
from os.path import join as pjoin
import numpy as np
import time
from tqdm import tqdm

from sklearn.decomposition import PCA
from sklearn.cluster import DBSCAN, KMeans
from sklearn.manifold import TSNE

import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

from torch.utils.data import Dataset, DataLoader

from argoverse.data_loading.argoverse_forecasting_loader import ArgoverseForecastingLoader
from argoverse.map_representation.map_api import ArgoverseMap


def visualize_centerline(ax, centerlines, orig, rot, color="grey") -> None:
    """Visualize the computed centerline.

    Args:
        centerline: Sequence of coordinates forming the centerline
    """
    for centerline in centerlines:
        centerline = np.matmul(rot, (centerline[:, :2] - orig.reshape(-1, 2)).T).T
        line_coords = list(zip(*centerline))
        lineX = line_coords[0]
        lineY = line_coords[1]
        ax.plot(lineX, lineY, "--", color=color, alpha=1, linewidth=1, zorder=0)
        ax.text(lineX[1], lineY[1], "s")
        ax.text(lineX[-2], lineY[-2], "e")
    # ax.axis("equal")


# Argoverse Target Agent Trajectory Loader
class ArgoversePreprocessor(Dataset):
    def __init__(self,
                 root_dir,
                 obs_horizon=20,
                 pred_horizon=30,
                 split="train",
                 viz=False):

        self.obs_horizon = obs_horizon
        self.pred_horizon = pred_horizon
        self.LANE_WIDTH = {'MIA': 3.84, 'PIT': 3.97}
        self.COLOR_DICT = {"AGENT": "#d33e4c", "OTHERS": "#d3e8ef", "AV": "#007672"}

        self.split = split
        self.am = ArgoverseMap()

        self.loader = ArgoverseForecastingLoader(pjoin(root_dir, split+"_obs" if split == "test" else split))

        self.viz = viz

        if self.viz:
            self.fig, self.axs = plt.subplots(2, 1)

    def __getitem__(self, idx):
        f_path = self.loader.seq_list[idx]
        seq = self.loader.get(f_path)
        dataframe = seq.seq_df
        city = dataframe["CITY_NAME"].values[0]

        agent_df = dataframe[dataframe.OBJECT_TYPE == "AGENT"].sort_values(by="TIMESTAMP")
        trajs = np.concatenate((agent_df.X.to_numpy().reshape(-1, 1), agent_df.Y.to_numpy().reshape(-1, 1)), 1)

        orig = trajs[self.obs_horizon-1].copy().astype(np.float32)

        # get the road centrelines
        lanes = self.am.find_local_lane_centerlines(orig[0], orig[1], city_name=city)

        # get the rotation
        lane_dir_vector, conf, nearest = self.am.get_lane_direction_traj(traj=trajs[:self.obs_horizon], city_name=city)

        if conf <= 0.1:
            lane_dir_vector = (orig - trajs[self.obs_horizon-4]) / 2.0
        theta = - np.arctan2(lane_dir_vector[1], lane_dir_vector[0])
        # print("pre: {};".format(pre))
        # print("theta: {};".format(theta))

        rot = np.asarray([
            [np.cos(theta), -np.sin(theta)],
            [np.sin(theta), np.cos(theta)]], np.float32)

        rot_ = np.asarray([
            [1, 0],
            [0, 1]
        ])

        agt_rot = np.matmul(rot, (trajs - orig.reshape(-1, 2)).T).T
        agt_ori = np.matmul(rot_, (trajs - orig.reshape(-1, 2)).T).T

        if self.viz:
            # plot original seq
            self.axs[0].plot(agt_ori[:self.obs_horizon, 0], agt_ori[:self.obs_horizon, 1], 'gx-')     # obs
            self.axs[0].plot(agt_ori[self.obs_horizon:, 0], agt_ori[self.obs_horizon:, 1], 'yx-')     # future
            self.axs[0].set_xlim([-120, 120])
            self.axs[0].set_ylim([-50, 50])

            visualize_centerline(self.axs[0], lanes, orig, rot_)
            visualize_centerline(self.axs[0], [nearest], orig, rot_, color='red')

            self.axs[0].set_title("The Original")

            # plot rotated seq
            self.axs[1].plot(agt_rot[:self.obs_horizon, 0], agt_rot[:self.obs_horizon, 1], 'gx-')     # obs
            self.axs[1].plot(agt_rot[self.obs_horizon:, 0], agt_rot[self.obs_horizon:, 1], 'yx-')     # future
            self.axs[1].set_xlim([-120, 120])
            self.axs[1].set_ylim([-50, 50])

            visualize_centerline(self.axs[1], lanes, orig, rot)
            visualize_centerline(self.axs[1], [nearest], orig, rot, color='red')

            self.axs[1].set_title("The Rotated")

            self.fig.show()
            self.fig.waitforbuttonpress()
            for ax in tuple(self.axs):
                ax.cla()

        return agt_rot.astype(np.float32)

    def __len__(self):
        return len(self.loader)


def main():
    # config for initializing loader
    root = "/media/Data/autonomous_driving/Argoverse/raw_data"
    visualize = True
    clustering = False

    # loader init
    dataset = ArgoversePreprocessor(root_dir=root, split="train", viz=visualize)
    if visualize:
        loader = DataLoader(dataset, batch_size=1, num_workers=1, shuffle=False, pin_memory=False, drop_last=False)
    else:
        loader = DataLoader(dataset, batch_size=16, num_workers=16, shuffle=False, pin_memory=False, drop_last=False)

    # load and plot the trajs
    fig, axs = plt.subplots(1, 1)
    traj_array_flatten = np.empty((0, 50 * 2), dtype=np.float)
    # load all the target agent trajectory and plot
    for i, traj_batch in enumerate(tqdm(loader)):
        (batch_size, _, _) = traj_batch.shape
        for batch_id in range(batch_size):
            axs.plot(traj_batch[batch_id, :20, 0], traj_batch[batch_id, :20, 1], c='b', alpha=0.01)     # plot observed traj
            axs.plot(traj_batch[batch_id, 20:, 0], traj_batch[batch_id, 20:, 1], c='r', alpha=0.01)     # plot future traj
        traj_array_flatten = np.vstack([traj_array_flatten, traj_batch.reshape(batch_size, -1)])
    plt.show(block=False)

    # Apply PCA to reduce the dimension
    # start_time = time.time()
    # embedding = PCA(n_components=50).fit_transform(traj_array_flatten)
    # print("Processing time of PCA: {}".format((time.time() - start_time)/60))

    # # Apply DSCAN
    # for eps in np.linspace(1, 3, 20):
    #     start_time = time.time()
    #     db = DBSCAN(eps=eps, min_samples=1000).fit(traj_array_flatten)
    #     core_samples_mask = np.zeros_like(db.labels_, dtype=bool)
    #     core_samples_mask[db.core_sample_indices_] = True
    #     labels = db.labels_
    #     n_clusters_ = len(set(labels)) - (1 if -1 in labels else 0)
    #
    #     print("\neps = {}".format(eps))
    #     print("Processing time of DBSCAN: {}".format((time.time() - start_time)/60))
    #     print("Num of Cluster: {}".format(n_clusters_))
    #     unique_labels = set(labels)
    #     for label in unique_labels:
    #         class_member_mask = (labels == label)
    #         print("Lable {}: No. of Trajectories: {};".format(label, sum(class_member_mask)))

    if clustering:
        # Display via t-SNE
        # start_time = time.time()
        traj_embedding = TSNE(n_components=20, init='pca').fit_transform(traj_array_flatten)
        # print("Processing time of t-SNE: {}".format((time.time() - start_time)/60))

        # Apply k-means
        n_clusters = 6
        kmeans = KMeans(n_clusters=n_clusters, random_state=0).fit(traj_embedding)
        labels = kmeans.labels_
        for idx in range(n_clusters):
            print("Number of instance: {}".format(np.sum(labels == idx)))

        # plot the cluster
        fig, axs = plt.subplots(n_clusters, 1)
        for idx in range(n_clusters):
            center = kmeans.cluster_centers_[idx].reshape(50, 2)
            axs[idx].plot(center[:, 0], center[:, 1], c='r', alpha=1)
            trajs = traj_array_flatten[labels == idx]
            trajs = trajs.reshape(trajs.shape[0], 50, 2)
            for traj in trajs:
                axs[idx].plot(traj[:, 0], traj[:, 1], c='g', alpha=0.01)
        plt.show()


if __name__ == "__main__":
    main()