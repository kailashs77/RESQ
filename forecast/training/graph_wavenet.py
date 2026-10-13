"""Graph WaveNet with fixed directed and learned adaptive supports."""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as f


def _row_normalize(matrix: torch.Tensor) -> torch.Tensor:
    return matrix / matrix.sum(dim=-1, keepdim=True).clamp_min(1e-6)


class GraphConvolution(nn.Module):
    def __init__(self, channels: int, supports: int, dropout: float) -> None:
        super().__init__()
        self.mix = nn.Conv2d(channels * (1 + supports * 2), channels, kernel_size=1)
        self.dropout = dropout

    def forward(self, x: torch.Tensor, supports: list[torch.Tensor]) -> torch.Tensor:
        parts = [x]
        for adjacency in supports:
            one = torch.einsum("bcnt,nm->bcmt", x, adjacency)
            two = torch.einsum("bcnt,nm->bcmt", one, adjacency)
            parts.extend((one, two))
        return f.dropout(self.mix(torch.cat(parts, dim=1)), self.dropout, self.training)


class GraphWaveNet(nn.Module):
    def __init__(
        self,
        adjacency: torch.Tensor,
        input_channels: int = 4,
        residual_channels: int = 24,
        skip_channels: int = 64,
        end_channels: int = 128,
        blocks: int = 2,
        layers_per_block: int = 3,
        dropout: float = 0.3,
    ) -> None:
        super().__init__()
        if adjacency.ndim != 2 or adjacency.shape[0] != adjacency.shape[1]:
            raise ValueError("adjacency must be square")
        self.num_nodes = adjacency.shape[0]
        self.receptive_field = 1 + blocks * (2**layers_per_block - 1)
        self.register_buffer("forward_support", _row_normalize(adjacency.float()))
        self.register_buffer("backward_support", _row_normalize(adjacency.float().T))
        self.node_left = nn.Parameter(torch.randn(self.num_nodes, 10) * 0.1)
        self.node_right = nn.Parameter(torch.randn(10, self.num_nodes) * 0.1)
        self.start = nn.Conv2d(input_channels, residual_channels, kernel_size=1)
        self.filters = nn.ModuleList()
        self.gates = nn.ModuleList()
        self.skips = nn.ModuleList()
        self.graphs = nn.ModuleList()
        self.norms = nn.ModuleList()
        for _ in range(blocks):
            for layer in range(layers_per_block):
                dilation = 2**layer
                self.filters.append(
                    nn.Conv2d(residual_channels, residual_channels, (1, 2), dilation=(1, dilation))
                )
                self.gates.append(
                    nn.Conv2d(residual_channels, residual_channels, (1, 2), dilation=(1, dilation))
                )
                self.skips.append(nn.Conv2d(residual_channels, skip_channels, kernel_size=1))
                self.graphs.append(GraphConvolution(residual_channels, 3, dropout))
                self.norms.append(nn.BatchNorm2d(residual_channels))
        self.end_one = nn.Conv2d(skip_channels, end_channels, kernel_size=1)
        self.end_two = nn.Conv2d(end_channels, 12, kernel_size=1)

    def forward(self, data: torch.Tensor) -> torch.Tensor:
        # [batch, time, nodes, features] -> [batch, horizon, nodes]
        if data.ndim != 4 or data.shape[2] != self.num_nodes:
            raise ValueError("input shape must be [batch, time, num_nodes, features]")
        x = data.permute(0, 3, 2, 1)
        if x.shape[-1] < self.receptive_field:
            x = f.pad(x, (self.receptive_field - x.shape[-1], 0, 0, 0))
        x = self.start(x)
        adaptive = f.softmax(f.relu(self.node_left @ self.node_right), dim=-1)
        supports = [self.forward_support, self.backward_support, adaptive]
        skip = None
        for filt, gate, skip_conv, graph, norm in zip(
            self.filters, self.gates, self.skips, self.graphs, self.norms, strict=True
        ):
            residual = x
            x = torch.tanh(filt(x)) * torch.sigmoid(gate(x))
            contribution = skip_conv(x)
            skip = (
                contribution
                if skip is None
                else skip[..., -contribution.shape[-1] :] + contribution
            )
            x = norm(graph(x, supports) + residual[..., -x.shape[-1] :])
        out = self.end_two(f.relu(self.end_one(f.relu(skip))))
        return out[..., -1]
