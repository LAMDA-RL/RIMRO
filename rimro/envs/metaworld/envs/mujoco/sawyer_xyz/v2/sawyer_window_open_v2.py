import mujoco
import numpy as np
from gymnasium.spaces import Box

from metaworld.envs import reward_utils
from metaworld.envs.asset_path_utils import full_v2_path_for
from metaworld.envs.mujoco.sawyer_xyz.sawyer_xyz_env import (
    SawyerXYZEnv,
    _assert_task_is_set,
)


class SawyerWindowOpenEnvV2(SawyerXYZEnv):
    """SawyerWindowOpenEnv.

    Motivation for V2:
        When V1 scripted policy failed, it was often due to limited path length.
    Changelog from V1 to V2:
        - (8/11/20) Updated to Byron's XML
        - (7/7/20) Added 3 element handle position to the observation
            (for consistency with other environments)
        - (6/15/20) Increased max_path_length from 150 to 200
    """

    TARGET_RADIUS = 0.05

    def __init__(self, render_mode=None, camera_name=None, camera_id=None):
        hand_low = (-0.5, 0.40, 0.05)
        hand_high = (0.5, 1, 0.5)
        obj_low = (-0.1, 0.7, 0.16)
        obj_high = (0.1, 0.9, 0.16)

        super().__init__(
            self.model_name,
            hand_low=hand_low,
            hand_high=hand_high,
            render_mode=render_mode,
            camera_name=camera_name,
            camera_id=camera_id,
        )

        self.init_config = {
            "obj_init_angle": np.array(
                [
                    0.3,
                ],
                dtype=np.float32,
            ),
            "obj_init_pos": np.array([-0.1, 0.785, 0.16], dtype=np.float32),
            "hand_init_pos": np.array([0, 0.4, 0.2], dtype=np.float32),
        }
        self.obj_init_pos = self.init_config["obj_init_pos"]
        self.obj_init_angle = self.init_config["obj_init_angle"]
        self.hand_init_pos = self.init_config["hand_init_pos"]

        goal_low = self.hand_low
        goal_high = self.hand_high

        self._random_reset_space = Box(
            np.array(obj_low),
            np.array(obj_high),
        )
        self.goal_space = Box(np.array(goal_low), np.array(goal_high))

        self.maxPullDist = 0.2
        self.target_reward = 1000 * self.maxPullDist + 1000 * 2

    @property
    def model_name(self):
        return full_v2_path_for("sawyer_xyz/sawyer_window_horizontal.xml")

    @_assert_task_is_set
    def evaluate_state(self, obs, action):
        (
            reward,
            tcp_to_obj,
            _,
            target_to_obj,
            object_grasped,
            in_place,
        ) = self.compute_reward(action, obs)

        info = {
            "success": float(target_to_obj <= self.TARGET_RADIUS),
            "near_object": float(tcp_to_obj <= 0.05),
            "grasp_success": 1.0,
            "grasp_reward": object_grasped,
            "in_place_reward": in_place,
            "obj_to_target": target_to_obj,
            "unscaled_reward": reward,
        }

        return reward, info

    def _get_pos_objects(self):
        return self._get_site_pos("handleOpenStart")

    def _get_quat_objects(self):
        return np.zeros(4)

    def render_reset(self, obs_info: dict):
        hand_state = obs_info['hand_state'].copy()
        goal_pos = obs_info['goal_pos'].copy()
        obj_pos = obs_info['obj_pos'].copy()

        self.render_reset_hand(hand_state=hand_state)
        self.obj_init_angle = self.init_config["obj_init_angle"]
        self._target_pos = goal_pos
        self.obj_init_pos = obj_pos

        self.prev_obs = self._get_curr_obs_combined_no_goal()
        self.model.body_pos[
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "window")
        ] = self.obj_init_pos
        self.window_handle_pos_init = self._get_pos_objects() + np.array(
            [0.2, 0.0, 0.0]
        )
        self.data.joint("window_slide").qpos = 0.2
        mujoco.mj_forward(self.model, self.data)

        for site in self._target_site_config:
            self._set_pos_site(*site)

        return self._get_obs()

    def reset_model(self):
        self._reset_hand()
        self.prev_obs = self._get_curr_obs_combined_no_goal()

        self.obj_init_pos = self._get_state_rand_vec()

        self._target_pos = self.obj_init_pos + np.array([0.2, 0.0, 0.0])
        self.model.body_pos[
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "window")
        ] = self.obj_init_pos

        self.window_handle_pos_init = self._get_pos_objects()
        self.data.joint("window_slide").qpos = 0.0
        mujoco.mj_forward(self.model, self.data)
        return self._get_obs()

    def compute_reward(self, actions, obs):
        del actions
        obj = self._get_pos_objects()
        tcp = self.tcp_center
        target = self._target_pos.copy()

        target_to_obj = obj[0] - target[0]
        target_to_obj = np.linalg.norm(target_to_obj)
        target_to_obj_init = self.obj_init_pos[0] - target[0]
        target_to_obj_init = np.linalg.norm(target_to_obj_init)

        in_place = reward_utils.tolerance(
            target_to_obj,
            bounds=(0, self.TARGET_RADIUS),
            margin=abs(target_to_obj_init - self.TARGET_RADIUS),
            sigmoid="long_tail",
        )

        handle_radius = 0.02
        tcp_to_obj = np.linalg.norm(obj - tcp)
        tcp_to_obj_init = np.linalg.norm(self.window_handle_pos_init - self.init_tcp)
        reach = reward_utils.tolerance(
            tcp_to_obj,
            bounds=(0, handle_radius),
            margin=abs(tcp_to_obj_init - handle_radius),
            sigmoid="long_tail",
        )
        tcp_opened = 0
        object_grasped = reach

        reward = 10 * reward_utils.hamacher_product(reach, in_place)
        return (reward, tcp_to_obj, tcp_opened, target_to_obj, object_grasped, in_place)
