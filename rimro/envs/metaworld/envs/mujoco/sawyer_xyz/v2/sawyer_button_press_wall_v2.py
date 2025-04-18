import mujoco
import numpy as np
from gymnasium.spaces import Box

from metaworld.envs import reward_utils
from metaworld.envs.asset_path_utils import full_v2_path_for
from metaworld.envs.mujoco.sawyer_xyz.sawyer_xyz_env import (
    SawyerXYZEnv,
    _assert_task_is_set,
)


class SawyerButtonPressWallEnvV2(SawyerXYZEnv):
    def __init__(self, render_mode=None, camera_name=None, camera_id=None):
        hand_low = (-0.5, 0.40, 0.05)
        hand_high = (0.5, 1, 0.5)
        obj_low = (-0.05, 0.85, 0.1149)
        obj_high = (0.05, 0.9, 0.1151)

        super().__init__(
            self.model_name,
            hand_low=hand_low,
            hand_high=hand_high,
            render_mode=render_mode,
            camera_name=camera_name,
            camera_id=camera_id,
        )

        self.init_config = {
            "obj_init_pos": np.array([0.0, 0.9, 0.115], dtype=np.float32),
            "hand_init_pos": np.array([0, 0.4, 0.2], dtype=np.float32),
        }
        self.goal = np.array([0, 0.84, 0.12])
        self.obj_init_pos = self.init_config["obj_init_pos"]
        self.hand_init_pos = self.init_config["hand_init_pos"]

        goal_low = self.hand_low
        goal_high = self.hand_high

        self._random_reset_space = Box(
            np.array(obj_low),
            np.array(obj_high),
        )

        self.goal_space = Box(np.array(goal_low), np.array(goal_high))

    @property
    def model_name(self):
        return full_v2_path_for("sawyer_xyz/sawyer_button_press_wall.xml")

    @_assert_task_is_set
    def evaluate_state(self, obs, action):
        (
            reward,
            tcp_to_obj,
            tcp_open,
            obj_to_target,
            near_button,
            button_pressed,
        ) = self.compute_reward(action, obs)

        info = {
            "success": float(obj_to_target <= 0.03),
            "near_object": float(tcp_to_obj <= 0.05),
            "grasp_success": float(tcp_open > 0),
            "grasp_reward": near_button,
            "in_place_reward": button_pressed,
            "obj_to_target": obj_to_target,
            "unscaled_reward": reward,
        }

        return reward, info

    @property
    def _target_site_config(self):
        return []

    def _get_id_main_object(self):
        return self.unwrapped.model.geom_name2id("btnGeom")

    def _get_pos_objects(self):
        button_top_xpos = self.get_body_com("button") + np.array([0.0, -0.193, 0.0])
        wall_xpos = self.get_body_com("wall")
        return np.r_[button_top_xpos, wall_xpos]

    def _get_quat_objects(self):
        button_top_quat = self.data.body("button").xquat
        wall_quat = self.data.body("wall").xquat
        return np.r_[button_top_quat, wall_quat]

    def _set_obj_xyz(self, pos):
        qpos = self.data.qpos.flat.copy()
        qvel = self.data.qvel.flat.copy()
        qpos[9] = pos
        qvel[9] = 0
        self.set_state(qpos, qvel)

    def reset_model(self):
        self._reset_hand()
        self._target_pos = self.goal.copy()
        self.obj_init_pos = self.init_config["obj_init_pos"]

        goal_pos = self._get_state_rand_vec()
        self.obj_init_pos = goal_pos

        self.model.body_pos[
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "box")
        ] = self.obj_init_pos

        self._set_obj_xyz(0)
        self._target_pos = self._get_site_pos("hole")

        self._obj_to_target_init = abs(
            self._target_pos[1] - self._get_site_pos("buttonStart")[1]
        )

        return self._get_obs()

    def compute_reward(self, action, obs):
        del action
        obj = obs[4:7]
        tcp = self.tcp_center

        tcp_to_obj = np.linalg.norm(obj - tcp)
        tcp_to_obj_init = np.linalg.norm(obj - self.init_tcp)
        obj_to_target = abs(self._target_pos[1] - obj[1])

        near_button = reward_utils.tolerance(
            tcp_to_obj,
            bounds=(0, 0.01),
            margin=tcp_to_obj_init,
            sigmoid="long_tail",
        )
        button_pressed = reward_utils.tolerance(
            obj_to_target,
            bounds=(0, 0.005),
            margin=self._obj_to_target_init,
            sigmoid="long_tail",
        )

        reward = 0.0
        if tcp_to_obj > 0.07:
            tcp_status = (1 - obs[3]) / 2.0
            reward = 2 * reward_utils.hamacher_product(tcp_status, near_button)
        else:
            reward = 2
            reward += 2 * (1 + obs[3])
            reward += 4 * button_pressed**2
        return (reward, tcp_to_obj, obs[3], obj_to_target, near_button, button_pressed)
