# Vendored from Fooocus — https://github.com/lllyasviel/Fooocus
# License: GPL-3.0 (see LICENSE in vendor/gpl/). Copyright (c) lllyasviel and contributors.
# Local modifications: none apparent.

class PatchSettings:
    def __init__(self,
                 sharpness=2.0,
                 adm_scaler_end=0.3,
                 positive_adm_scale=1.5,
                 negative_adm_scale=0.8,
                 controlnet_softness=0.25,
                 adaptive_cfg=7.0):
        self.sharpness = sharpness
        self.adm_scaler_end = adm_scaler_end
        self.positive_adm_scale = positive_adm_scale
        self.negative_adm_scale = negative_adm_scale
        self.controlnet_softness = controlnet_softness
        self.adaptive_cfg = adaptive_cfg
        self.global_diffusion_progress = 0
        self.eps_record = None

