import mujoco
import numpy as np
from gymnasium.spaces import Box
from scipy.spatial.transform import Rotation

from metaworld.envs import reward_utils
from metaworld.envs.asset_path_utils import full_v2_path_for
from metaworld.envs.mujoco.sawyer_xyz.sawyer_xyz_env import (
    SawyerXYZEnv,
    _assert_task_is_set,
)


class SawyerPlateSlideBackSideEnvV2(SawyerXYZEnv):
    """SawyerPlateSlideBackSideEnv.

    Motivation for V2:
        In V1, the cabinet was lifted .02 units off the ground. In order for the
        end effector to move the plate without running into the cabinet, its
        movements had to be very precise. These precise movements become
        very difficult as soon as noise is introduced to the action space
        (success rate dropped from 100% to 20%).
    Changelog from V1 to V2:
        - (8/7/20) Switched to Byron's XML
        - (7/7/20) Added 3 element cabinet position to the observation
            (for consistency with other environments)
        - (6/22/20) Cabinet now sits on ground, instead of .02 units above it
    """

    def __init__(self, render_mode=None, camera_name=None, camera_id=None):
        goal_low = (-0.05, 0.6, 0.015)
        goal_high = (0.15, 0.6, 0.015)
        hand_low = (-0.5, 0.40, 0.05)
        hand_high = (0.5, 1, 0.5)
        obj_low = (-0.25, 0.6, 0.0)
        obj_high = (-0.25, 0.6, 0.0)

        super().__init__(
            self.model_name,
            hand_low=hand_low,
            hand_high=hand_high,
            render_mode=render_mode,
            camera_name=camera_name,
            camera_id=camera_id,
        )

        self.init_config = {
            "obj_init_angle": 0.3,
            "obj_init_pos": np.array([-0.25, 0.6, 0.02], dtype=np.float32),
            "hand_init_pos": np.array((0, 0.6, 0.2), dtype=np.float32),
        }
        self.goal = np.array([0.0, 0.6, 0.015])
        self.obj_init_pos = self.init_config["obj_init_pos"]
        self.obj_init_angle = self.init_config["obj_init_angle"]
        self.hand_init_pos = self.init_config["hand_init_pos"]

        self._random_reset_space = Box(
            np.hstack((obj_low, goal_low)),
            np.hstack((obj_high, goal_high)),
        )
        self.goal_space = Box(np.array(goal_low), np.array(goal_high))

    @property
    def model_name(self):
        return full_v2_path_for("sawyer_xyz/sawyer_plate_slide_sideway.xml")

    @_assert_task_is_set
    def evaluate_state(self, obs, action):
        (
            reward,
            tcp_to_obj,
            tcp_opened,
            obj_to_target,
            object_grasped,
            in_place,
        ) = self.compute_reward(action, obs)

        success = float(obj_to_target <= 0.07)
        near_object = float(tcp_to_obj <= 0.03)

        info = {
            "success": success,
            "near_object": near_object,
            "grasp_success": 0.0,
            "grasp_reward": object_grasped,
            "in_place_reward": in_place,
            "obj_to_target": obj_to_target,
            "unscaled_reward": reward,
        }
        return reward, info

    def _get_pos_objects(self):
        return self.data.geom("puck").xpos

    def _get_quat_objects(self):
        geom_xmat = self.data.geom("puck").xmat.reshape(3, 3)
        return Rotation.from_matrix(geom_xmat).as_quat()

    def _get_obs_dict(self):
        return dict(
            state_observation=self._get_obs(),
            state_desired_goal=self._target_pos,
            state_achieved_goal=self._get_pos_objects(),
        )

    def _set_obj_xyz(self, pos):
        qpos = self.data.qpos.flat.copy()
        qvel = self.data.qvel.flat.copy()
        qpos[9:11] = pos
        self.set_state(qpos, qvel)

    def reset_model(self):
        self._reset_hand()

        self.obj_init_pos = self.init_config["obj_init_pos"]
        self._target_pos = self.goal.copy()

        rand_vec = self._get_state_rand_vec()
        self.obj_init_pos = rand_vec[:3]
        self._target_pos = rand_vec[3:]
        self.model.body_pos[
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "puck_goal")
        ] = self.obj_init_pos
        self._set_obj_xyz(np.array([-0.15, 0.0]))

        return self._get_obs()

    def compute_reward(self, actions, obs):
        _TARGET_RADIUS = 0.05
        tcp = self.tcp_center
        obj = obs[4:7]
        tcp_opened = obs[3]
        target = self._target_pos

        obj_to_target = np.linalg.norm(obj - target)
        in_place_margin = np.linalg.norm(self.obj_init_pos - target)
        in_place = reward_utils.tolerance(
            obj_to_target,
            bounds=(0, _TARGET_RADIUS),
            margin=in_place_margin - _TARGET_RADIUS,
            sigmoid="long_tail",
        )

        tcp_to_obj = np.linalg.norm(tcp - obj)
        obj_grasped_margin = np.linalg.norm(self.init_tcp - self.obj_init_pos)
        object_grasped = reward_utils.tolerance(
            tcp_to_obj,
            bounds=(0, _TARGET_RADIUS),
            margin=obj_grasped_margin - _TARGET_RADIUS,
            sigmoid="long_tail",
        )

        reward = 1.5 * object_grasped

        if tcp[2] <= 0.03 and tcp_to_obj < 0.07:
            reward = 2 + (7 * in_place)

        if obj_to_target < _TARGET_RADIUS:
            reward = 10.0
        return [reward, tcp_to_obj, tcp_opened, obj_to_target, object_grasped, in_place]
