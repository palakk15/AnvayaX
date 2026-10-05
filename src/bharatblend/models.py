"""Native additive routing and an optional spatial-attention accuracy challenger."""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax

from .calibration import raw_mean


def raw_features(case, sources):
    o = case.observation
    fs = list(case.forecasts.values())
    lead = np.mean([(f.valid_start - f.initialization_time).total_seconds() / 3600 for f in fs])
    phase = o.valid_start.timetuple().tm_yday * 2 * np.pi / 365.25
    means = [float(np.linalg.norm(raw_mean(f))) if o.variable == "wind_uv" else float(raw_mean(f)) for f in fs]
    data = {"lead.hours": lead, "lead.duration": (o.valid_end - o.valid_start).total_seconds() / 3600,
            "season.sin": np.sin(phase), "season.cos": np.cos(phase),
            "geography.latitude": o.latitude, "geography.longitude": o.longitude,
            "geography.elevation": o.elevation_m, "geography.coast": o.coastal_distance_km,
            "regime.predicted_mean": np.mean(means), "regime.predicted_max": np.max(means),
            "disagreement.std": np.std(means)}
    for name in ("cape", "u850", "v850", "q850", "mslp", "moisture_flux", "vorticity700"):
        available = [f.context[name] for f in fs if name in f.context]
        data[f"regime.{name}"] = float(np.mean(available)) if available else 0.0
        data[f"availability.context_{name}"] = float(bool(available))
    for s in sources:
        f = case.forecasts.get(s)
        data[f"availability.{s}"] = float(f is not None)
        data[f"recent_skill.{s}"] = case.recent_skill.get(s, 1.0)
        data[f"age.{s}"] = (case.issuance_time - f.initialization_time).total_seconds() / 3600 if f else 0
        # Missing values are neutral with a separate explicit availability flag.
        value = float(np.linalg.norm(raw_mean(f))) if f and o.variable == "wind_uv" else float(raw_mean(f)) if f else 0
        data[f"regime.source_{s}"] = value
        members = np.asarray(f.value) if f else np.asarray([0.0])
        data[f"disagreement.spread_{s}"] = float(np.mean(np.std(members, axis=0))) if members.ndim else 0.0
    data["interaction.lead_regime"] = lead * float(np.mean(means)) / 240
    data["interaction.latitude_season"] = o.latitude * np.sin(phase)
    return data


class FeatureMap:
    def __init__(self, sources, removed_groups=()):
        self.sources = list(sources)
        self.removed_groups = list(removed_groups)

    def fit(self, cases):
        dependencies = {"interaction.lead_regime": {"lead", "regime"},
                        "interaction.latitude_season": {"geography", "season"}}
        self.names = [n for n in raw_features(cases[0], self.sources)
                      if n.split(".")[0] not in self.removed_groups
                      and not dependencies.get(n, set()).intersection(self.removed_groups)]
        x = self.raw(cases)
        self.mean = x.mean(axis=0)
        self.scale = np.maximum(x.std(axis=0), 1e-3)
        self.basis_names = ["intercept"] + [f"{n}/{basis}" for n in self.names
                                            for basis in ("linear", "hinge_low", "hinge_mid", "hinge_high")]
        self.center = self._basis(x).mean(axis=0)
        self.center[0] = 0
        return self

    def raw(self, cases):
        rows = [raw_features(c, self.sources) for c in cases]
        return np.asarray([[row[n] for n in self.names] for row in rows])

    def _basis(self, x):
        z = np.clip((x - self.mean) / self.scale, -5, 5)
        splines = np.stack([z, np.maximum(z + 1, 0), np.maximum(z, 0), np.maximum(z - 1, 0)], axis=-1)
        return np.column_stack([np.ones(len(x)), splines.reshape(len(x), -1)])

    def transform(self, cases):
        return self._basis(self.raw(cases)) - self.center

    def to_dict(self):
        return {"sources": self.sources, "removed_groups": self.removed_groups, "names": self.names,
                "basis_names": self.basis_names, "mean": self.mean.tolist(), "scale": self.scale.tolist(),
                "center": self.center.tolist()}

    @classmethod
    def from_dict(cls, data):
        obj = cls(data["sources"], data["removed_groups"])
        for key in ("names", "basis_names"):
            setattr(obj, key, data[key])
        for key in ("mean", "scale", "center"):
            setattr(obj, key, np.asarray(data[key]))
        return obj


def masked_weights(logits, mask):
    logits = np.asarray(logits, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    if np.any(mask.sum(axis=-1) == 0):
        raise ValueError("Cannot normalize weights without an eligible source")
    return softmax(np.where(mask, logits, -1e12), axis=-1)


class AdditiveGate:
    def __init__(self, sources, regularization=0.01, removed_groups=(), static=False):
        self.sources = list(sources)
        self.regularization = regularization
        self.static = static
        self.features = FeatureMap(sources, removed_groups)

    def fit(self, cases, q, b, mask, iterations=120):
        self.features.fit(cases)
        x = self.features.transform(cases)
        if self.static:
            x = x[:, :1]
        n, p = x.shape
        m = len(self.sources)
        def objective(flat):
            beta = flat.reshape(p, m)
            w = masked_weights(x @ beta, mask)
            qw = np.einsum("nij,nj->ni", q, w)
            loss = np.mean(np.sum(w * qw - 2 * b * w, axis=1))
            dw = 2 * (qw - b) / n
            dz = w * (dw - np.sum(dw * w, axis=1, keepdims=True))
            penalty = beta.copy()
            penalty[0] = 0
            return float(loss + self.regularization * np.sum(penalty ** 2)), (x.T @ dz + 2 * self.regularization * penalty).ravel()
        result = minimize(objective, np.zeros(p * m), method="L-BFGS-B", jac=True,
                          options={"maxiter": iterations, "ftol": 1e-9})
        self.coefficients = result.x.reshape(p, m)
        # Softmax invariant: center coefficients over experts for identifiable receipts.
        self.coefficients -= self.coefficients.mean(axis=1, keepdims=True)
        self.optimization = {"converged": bool(result.success), "iterations": int(result.nit), "message": str(result.message)}
        return self

    def predict(self, cases, mask):
        x = self.features.transform(cases)
        if self.static:
            x = x[:, :1]
        logits = x @ self.coefficients
        return masked_weights(logits, mask), logits

    def explanation(self, case):
        row = self.features.transform([case])[0]
        names = self.features.basis_names
        if self.static:
            row, names = row[:1], names[:1]
        contributions = row[:, None] * self.coefficients
        answer = {}
        for m, source in enumerate(self.sources):
            terms = {}
            for name, value in zip(names, contributions[:, m]):
                feature = name.split("/")[0]
                terms[feature] = terms.get(feature, 0.0) + float(value)
            answer[source] = terms
        return answer

    def to_dict(self):
        return {"sources": self.sources, "regularization": self.regularization, "static": self.static,
                "features": self.features.to_dict(), "coefficients": self.coefficients.tolist(),
                "optimization": self.optimization}

    @classmethod
    def from_dict(cls, data):
        obj = cls(data["sources"], data["regularization"], static=data["static"])
        obj.features = FeatureMap.from_dict(data["features"])
        obj.coefficients = np.asarray(data["coefficients"])
        obj.optimization = data["optimization"]
        return obj


def spatial_patches(cases, features, neighbors=4):
    """Nearby contexts with identical issue and valid windows; never include labels."""
    groups = {}
    for i, c in enumerate(cases):
        o = c.observation
        groups.setdefault((c.issuance_time, o.valid_start, o.valid_end, o.variable), []).append(i)
    patches = np.zeros((len(cases), neighbors, features.shape[1]), dtype=np.float32)
    padding = np.ones((len(cases), neighbors), dtype=bool)
    for indices in groups.values():
        xy = np.asarray([[cases[i].observation.latitude, cases[i].observation.longitude] for i in indices])
        for local, index in enumerate(indices):
            near = np.argsort(np.sum((xy - xy[local]) ** 2, axis=1), kind="stable")[:neighbors]
            chosen = [indices[j] for j in near]
            patches[index, :len(chosen)] = features[chosen]
            padding[index, :len(chosen)] = False
    return patches, padding


def _attention_network(feature_count, source_count):
    from torch import nn
    class Network(nn.Module):
        def __init__(self):
            super().__init__()
            self.project = nn.Linear(feature_count, 32)
            layer = nn.TransformerEncoderLayer(32, 4, 64, dropout=0.0, batch_first=True, activation="gelu")
            self.encoder = nn.TransformerEncoder(layer, 1, enable_nested_tensor=False)
            self.out = nn.Linear(32, source_count)

        def forward(self, patches, padding):
            hidden = self.encoder(self.project(patches), src_key_padding_mask=padding)
            return self.out(hidden[:, 0])
    return Network()


class SpatialGate:
    """Small local spatial transformer, not a claimed reproduction of published MoWE."""
    def __init__(self, sources, features):
        self.sources = list(sources)
        self.features = features

    def fit(self, cases, q, b, mask, epochs=60, seed=42, dropout=0.15):
        import torch
        torch.set_num_threads(2)
        torch.manual_seed(seed)
        x = self.features.transform(cases)
        patches, padding = spatial_patches(cases, x)
        self.network = _attention_network(x.shape[1], len(self.sources))
        optimizer = torch.optim.AdamW(self.network.parameters(), lr=0.003, weight_decay=0.02)
        pt, pad = torch.tensor(patches), torch.tensor(padding)
        qt, bt, available = torch.tensor(q, dtype=torch.float32), torch.tensor(b, dtype=torch.float32), torch.tensor(mask)
        generator = np.random.default_rng(seed)
        self.network.train()
        for _ in range(epochs):
            kept = available.clone()
            if dropout:
                # Output dropout tests normalization; input context dropout is a separately logged ablation.
                dropped = torch.tensor(generator.random(mask.shape) > dropout)
                proposed = kept & dropped
                kept = torch.where(proposed.any(dim=1, keepdim=True), proposed, kept)
            weights = torch.softmax(self.network(pt, pad).masked_fill(~kept, -1e9), dim=1)
            loss = (torch.einsum("ni,nij,nj->n", weights, qt, weights) - 2 * (weights * bt).sum(dim=1)).mean()
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.network.parameters(), 1.0)
            optimizer.step()
        self.network.eval()
        self.metadata = {"epochs": epochs, "seed": seed, "source_output_dropout": dropout,
                         "architecture": "one_layer_local_spatial_transformer", "neighbors": 4}
        return self

    def predict(self, cases, mask):
        import torch
        patches, padding = spatial_patches(cases, self.features.transform(cases))
        with torch.no_grad():
            logits = self.network(torch.tensor(patches), torch.tensor(padding)).numpy()
        return masked_weights(logits, mask), logits

    def save(self, path):
        import torch
        torch.save(self.network.state_dict(), path)

    @classmethod
    def load(cls, path, sources, features):
        import torch
        torch.set_num_threads(2)
        obj = cls(sources, features)
        obj.network = _attention_network(len(features.basis_names), len(sources))
        obj.network.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
        obj.network.eval()
        return obj
