import os

import numpy as np
import torch
from datetime import datetime

from ..Dataset.DatasetTransforms import Compose, HorizontalTransform, VerticalTransform, ToTensorTransform, AddBatchTransform


class GomokuDataset(torch.utils.data.Dataset):

    def __init__(self, transforms: Compose = None,
                 data: list = [],
                 name: str = "Default GomokuDataset name"):

        self.name = name
        self.transforms = transforms or Compose([
            HorizontalTransform(0.5),
            VerticalTransform(0.5),
            ToTensorTransform()         # Cannot apply sym transforms on Tensor
        ])
        self.all_data_transforms = [
            Compose([]),
            Compose([VerticalTransform(1)]),
            Compose([HorizontalTransform(1)]),
            Compose([HorizontalTransform(1), VerticalTransform(1)]),
        ]

        self.data = data
        self.samples_generated = len(data)

    def __str__(self):
        return self.name

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        _, inputs, policy, value = self.data[idx]

        inputs = self.transforms(inputs)
        policy = self.transforms.repeat(policy)

        return inputs, (policy, value)

    def extend(self, lst: list):
        self.data.extend(lst)
        self.samples_generated += len(lst)

    def add(self, samples: list):
        self.extend(samples)

        # Add all samples we can create with ?
        # for s in samples:
        #     self.extend([  # Add new samples made with each sample
        #         (s[0], t(s[1]), t.repeat(s[2]), s[3]) # Create new sample with this Compose
        #         for t in self.all_data_transforms
        #     ])

    def bounded_add(self, samples: list, max_length: int):

        self.add(samples)
        if len(self.data) > max_length:
            self.data = self.data[len(self.data) - max_length:]
