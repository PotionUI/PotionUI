"""Shared generator/loader toolkit.

Common building blocks used by generator and model/checkpoint loader pipes so
each model-specific pipe only needs to implement the part that's actually
model-specific ("generate one item", "load this model").
"""
