import os
import random
from collections import deque

import numpy as np


class Agent:
    def predict(self, state, action_mask=None):
        raise NotImplementedError

    def reset():
        pass

class RuleAgent(Agent):
    def __init__(self, num_threats=20):
        self.max_threats = num_threats
        self.current_allocation = np.zeros(num_threats)
        self.pre_allocation = None
        self.index = 0
        self.pre_allocation = None

    def predict(self, state, action_mask=None):
        if self.pre_allocation is None:
            # threat_levels, pre_allocation, current_allocation = state.reshape([3, -1])
            # self.threat_levels = state["observations"][: self.max_threats]
            self.threat_levels = state[: self.max_threats]
            self.pre_allocation=self.calculate_pre_allocation()

        if not np.array_equal(self.pre_allocation, self.pre_allocation) or np.array_equal(
            self.current_allocation, self.pre_allocation
        ):
            self.reset(self.pre_allocation)

        while self.index < self.max_threats:
            if self.current_allocation[self.index] < self.pre_allocation[self.index]:
                self.current_allocation[self.index] += 1
                return self.index
            self.index += 1

    def reset(self, allocation):
        self.pre_allocation = allocation.copy()
        self.current_allocation = np.zeros(self.max_threats)
        self.index = 0

    def calculate_pre_allocation(self,num_drones=20):
        pre_allocation = np.zeros(self.max_threats, dtype=int)
        remaining_drones = num_drones

        # Allocate one drone to each non-zero threat
        for i, threat in enumerate(self.threat_levels):
            if threat > 0 and remaining_drones > 0:
                pre_allocation[i] += 1
                remaining_drones -= 1

        # Allocate remaining drones to highest threats
        while remaining_drones > 0:
            for i in range(self.max_threats):
                if self.threat_levels[i] > 0 and remaining_drones > 0:
                    pre_allocation[i] += 1
                    remaining_drones -= 1
                if remaining_drones == 0:
                    break

        return pre_allocation


class RandomAgent(Agent):
    def __init__(self, num_threats=20, seed=None):
        self.num_threats = num_threats
        self.seed = seed
        if seed is not None:
            np.random.seed(seed)

    def predict(self, state, action_mask):
        return np.random.choice(np.where(action_mask)[0])


class RllibAgent(Agent):
    def __init__(self, ckpt_path: str, explore: bool = False):
        from ray.rllib.policy import Policy

        policies = Policy.from_checkpoint(ckpt_path)
        if isinstance(policies, dict):
            self.policy = policies["default_policy"]
        else:
            self.policy = policies

        self.state = self.policy.get_initial_state()
        self.explore = explore
        self.mask_obs = self.policy.config.get("env_config", {}).get("mask_obs", False)

    def reset(self):
        self.state = self.policy.get_initial_state()

    def predict(self, obs, action_mask=None):
        if self.mask_obs and (not isinstance(obs, dict) or "action_mask" not in obs):
            obs = {
                "observations": obs,
                "action_mask": action_mask,
            }

        action, self.state, _ = self.policy.compute_single_action(
            obs, self.state, explore=self.explore
        )
        return action


class Sb3Agent(Agent):
    def __init__(self, algorithm: str, ckpt: str, deterministic: bool = False):
        import sb3_contrib
        import stable_baselines3 as sb3
        from stable_baselines3.common.base_class import BaseAlgorithm

        algo_cls: BaseAlgorithm

        if hasattr(sb3, algorithm):
            algo_cls = getattr(sb3, algorithm)
        elif hasattr(sb3_contrib, algorithm):
            algo_cls = getattr(sb3_contrib, algorithm)
        else:
            raise ValueError(
                f"Algorithm {algorithm} not found in stable_baselines3 or sb3_contrib"
            )

        self.model = algo_cls.load(ckpt)
        self.state = None
        self.deterministic = deterministic

    def reset(self):
        self.state = None

    def predict(self, obs, action_mask=None):
        if action_mask is not None:
            action, self.state = self.model.predict(
                obs,
                self.state,
                deterministic=self.deterministic,
            )
        else:
            action, self.state = self.model.predict(
                obs,
                self.state,
                deterministic=self.deterministic,
                action_masks=action_mask,
            )

        return action


class ReplayBuffer:
    def __init__(self, capacity):
        self.buffer = deque(maxlen=capacity)

    def push(self, initial_state, joint_action, reward, final_state, done):
        self.buffer.append((initial_state, joint_action, reward, final_state, done))

    def sample(self, batch_size):
        return random.sample(self.buffer, batch_size)

    def clear(self):
        self.buffer.clear()

    def __len__(self):
        return len(self.buffer)


def iql_agent():
    try:
        import torch
        import torch.nn as nn
        import torch.nn.functional as F
        import torch.optim as optim
    except ImportError:
        return Agent

    class QNetwork(nn.Module):
        def __init__(self, state_dim, action_dim):
            super(QNetwork, self).__init__()
            self.fc1 = nn.Linear(state_dim, 256)
            self.fc2 = nn.Linear(256, 256)
            self.fc3 = nn.Linear(256, action_dim)

        def forward(self, x):
            x = F.relu(self.fc1(x))
            x = F.relu(self.fc2(x))
            return self.fc3(x)

    class IQLAgent:
        def __init__(
            self,
            state_dim,
            action_dim,
            lr=1e-5,
            gamma=0.99,
            epsilon_start=1.0,
            epsilon_end=0.01,
            epsilon_decay=0.995,
            _limit=3,
        ):
            self.state_dim = state_dim
            self.action_dim = action_dim
            self.q_network = QNetwork(state_dim, action_dim)
            self.target_network = QNetwork(state_dim, action_dim)
            self.target_network.load_state_dict(self.q_network.state_dict())
            self.optimizer = optim.Adam(self.q_network.parameters(), lr=lr)
            self.gamma = gamma
            self.epsilon = epsilon_start
            self.epsilon_end = epsilon_end
            self.epsilon_decay = epsilon_decay

            self._limit = _limit
            self._current_allocation = np.zeros(20)

        def save_checkpoint(self, episode, path="checkpoints"):
            if not os.path.exists(path):
                os.makedirs(path)

            checkpoint = {
                "episode": episode,
                "q_network_state_dict": self.q_network.state_dict(),
                "target_network_state_dict": self.target_network.state_dict(),
                "optimizer_state_dict": self.optimizer.state_dict(),
                "epsilon": self.epsilon,
            }

            checkpoint_path = os.path.join(path, f"checkpoint_episode_{episode}.pth")
            torch.save(checkpoint, checkpoint_path)
            print(f"Checkpoint saved at episode {episode}")

        def load_checkpoint(self, checkpoint_path):
            if not os.path.exists(checkpoint_path):
                print(f"Checkpoint file not found: {checkpoint_path}")
                return None

            checkpoint = torch.load(checkpoint_path)

            self.q_network.load_state_dict(checkpoint["q_network_state_dict"])
            self.target_network.load_state_dict(checkpoint["target_network_state_dict"])
            self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
            self.epsilon = checkpoint["epsilon"]

            print(f"Checkpoint loaded from episode {checkpoint['episode']}")
            return checkpoint["episode"]

        def predict(self, state, action_mask):
            threat_levels, pre_allocation, current_allocation = state.reshape([3, -1])
            if np.sum(self._current_allocation) == np.sum(pre_allocation):
                self._current_allocation = np.zeros(len(threat_levels))

            if self._limit is not None:
                redundant_mask = self._current_allocation >= self._limit
                action_mask = action_mask & ~redundant_mask

            with torch.no_grad():
                q_values = self.q_network(torch.FloatTensor(state)).numpy()
                q_values[~action_mask] = -np.inf
                action = np.argmax(q_values)
                self._current_allocation[action] += 1
                return action

        def select_action(self, state, action_mask):
            if random.random() > self.epsilon:
                return self.predict(state, action_mask)
            else:
                valid_actions = np.where(action_mask)[0]
                return np.random.choice(valid_actions)

        def update(self, batch):
            states, actions, rewards, next_states, dones = zip(*batch)

            states = torch.FloatTensor(np.array(states))
            actions = torch.LongTensor(np.array(actions))
            rewards = torch.FloatTensor(rewards)
            next_states = torch.FloatTensor(np.array(next_states))
            dones = torch.FloatTensor(dones)

            current_q_values = self.q_network(states).gather(1, actions.unsqueeze(1))
            with torch.no_grad():
                next_q_values = self.target_network(next_states).max(1)[0]
                target_q_values = rewards + (1 - dones) * self.gamma * next_q_values

            loss = F.mse_loss(current_q_values.squeeze(), target_q_values)
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            return loss.item()

        def update_target_network(self):
            self.target_network.load_state_dict(self.q_network.state_dict())

        def update_epsilon(self):
            self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)

    return IQLAgent


IQLAgent = iql_agent()


__all__ = [
    "Agent",
    "RuleAgent",
    "RandomAgent",
    "ReplayBuffer",
    "RllibAgent",
    "IQLAgent",
]
