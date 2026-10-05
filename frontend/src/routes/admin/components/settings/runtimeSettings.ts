export type RuntimeSettingApplies = 'live' | 'next_load' | 'restart';
export type RuntimeSettingGroup = 'speed' | 'memory' | 'debug' | 'diagnostics';
export type RuntimeSettingScope = 'app' | 'backend';
export type RuntimeSettingKind = 'bool' | 'choice' | 'float' | 'int';
export type RuntimeSettingValue = boolean | string | number;

export interface RuntimeSettingChoice {
	value: string;
	label: string;
}

export interface RuntimeSettingDescriptor {
	key: string;
	applies: RuntimeSettingApplies;
	group: RuntimeSettingGroup;
	scope: RuntimeSettingScope;
	kind: RuntimeSettingKind;
	control?: 'segmented' | 'select';
	label: string;
	description: string;
	details?: string;
	guidance: string;
	defaultLabel: string;
	defaultValue: RuntimeSettingValue;
	choices?: RuntimeSettingChoice[];
	min?: number;
	max?: number;
	step?: number;
	unit?: string;
	parent?: string;
}

export const RUNTIME_SETTINGS: RuntimeSettingDescriptor[] = [
	{
		key: 'native_fp8_matmul',
		applies: 'live',
		group: 'speed',
		scope: 'backend',
		kind: 'bool',
		label: 'fp8 fast multiply',
		description:
			"Multiplies fp8 checkpoints directly on the GPU's fp8 tensor cores instead of converting the weights first.",
		guidance:
			'Faster on RTX 40 and 50 series; older GPUs ignore it. Turn it off if you see errors or wrong colours with fp8 models.',
		defaultLabel: 'Off',
		defaultValue: false
	},
	{
		key: 'native_nvfp4_matmul',
		applies: 'live',
		group: 'speed',
		scope: 'backend',
		kind: 'bool',
		label: 'nvfp4 fast multiply',
		description: "Multiplies nvfp4 checkpoints directly on the GPU's fp4 tensor cores.",
		guidance:
			'Needs an RTX 50 series GPU; others ignore it. It can change the output slightly, so turn it off if results look worse.',
		defaultLabel: 'Off',
		defaultValue: false
	},
	{
		key: 'native_lora_fused',
		applies: 'live',
		group: 'speed',
		scope: 'backend',
		kind: 'bool',
		label: 'Fuse LoRAs into the model',
		description:
			'Merges the active LoRAs into the model weights once per run, so each step costs the same as without LoRAs.',
		guidance: 'Leave on. Turn it off only to compare against the slower per-step path when a LoRA looks wrong.',
		defaultLabel: 'On',
		defaultValue: true
	},
	{
		key: 'native_torch_compile',
		applies: 'next_load',
		group: 'speed',
		scope: 'backend',
		kind: 'bool',
		label: 'Compile the model (torch.compile)',
		description: 'Compiles the diffusion model when it fits fully on the GPU.',
		guidance:
			'The first run after a model loads is slower while it compiles; later runs are faster. Turn it off if loading fails or a model behaves differently.',
		defaultLabel: 'Off',
		defaultValue: false
	},
	{
		key: 'native_qwen3_te_bf16',
		applies: 'live',
		group: 'speed',
		scope: 'backend',
		kind: 'bool',
		label: 'Qwen3 text encoder in bf16',
		description:
			'Runs the Qwen3 text encoder (Flux 2, Z-Image, Krea-2, Anima, MiniMax-H3) in bf16 instead of fp32.',
		guidance:
			'Faster, and lets nvfp4 text encoders use the fast multiply, but prompts are encoded slightly less precisely. Turn it on if prompt encoding is slow.',
		defaultLabel: 'Off',
		defaultValue: false
	},
	{
		key: 'native_sol_attn_backend',
		applies: 'restart',
		group: 'speed',
		scope: 'backend',
		kind: 'choice',
		label: 'Sol-Attn implementation',
		description:
			'Flex uses PyTorch flex attention and works wherever Sol-Attn does. Kernel uses the compiled Sol-Attn kernel.',
		guidance:
			'Kernel can be faster but needs the kernel installed; if it is missing, Sol-Attn turns itself off for the session.',
		defaultLabel: 'Flex',
		defaultValue: 'flex',
		choices: [
			{ value: 'flex', label: 'Flex' },
			{ value: 'kernel', label: 'Kernel' }
		]
	},
	{
		key: 'native_attention_backend',
		applies: 'live',
		group: 'speed',
		scope: 'backend',
		kind: 'choice',
		control: 'select',
		label: 'Attention backend',
		description: 'The attention kernel the engine uses. Automatic picks the fastest one installed where the model runs.',
		guidance: 'Leave it on Automatic unless runs fail or look wrong with the backend it picks. A pinned backend must be installed on the worker.',
		defaultLabel: 'Automatic',
		defaultValue: '',
		choices: [
			{ value: '', label: 'Automatic' },
			{ value: 'sdpa', label: 'sdpa' },
			{ value: 'sage', label: 'sage' },
			{ value: 'sage2', label: 'sage2' },
			{ value: 'sage3', label: 'sage3' },
			{ value: 'flash', label: 'flash' },
			{ value: 'sparge', label: 'sparge' }
		]
	},
	{
		key: 'native_stream_prefetch',
		applies: 'live',
		group: 'memory',
		scope: 'backend',
		kind: 'bool',
		label: 'Prefetch streamed layers',
		description:
			'When a model does not fit in VRAM and streams from RAM, copies the next layer to the GPU while the current one computes.',
		guidance:
			'Usually faster for large models on smaller GPUs. Turn it off if you see out-of-memory errors while a model streams.',
		defaultLabel: 'Off',
		defaultValue: false
	},
	{
		key: 'native_fp8_quantize',
		applies: 'next_load',
		group: 'memory',
		scope: 'backend',
		kind: 'choice',
		label: 'Shrink bf16 models to fp8 at load',
		description:
			'Auto converts a bf16 diffusion model to fp8 only when that lets it fit fully in VRAM. Always converts every one. Off never converts.',
		guidance: 'Always saves the most VRAM at a small cost in quality.',
		defaultLabel: 'Auto',
		defaultValue: 'auto',
		choices: [
			{ value: 'auto', label: 'Auto' },
			{ value: 'off', label: 'Off' },
			{ value: 'force', label: 'Always' }
		]
	},
	{
		key: 'native_min_inference_memory_gb',
		applies: 'live',
		group: 'memory',
		scope: 'backend',
		kind: 'float',
		label: 'VRAM reserve for sampling',
		description: 'VRAM kept free beyond the model weights for the sampling step itself.',
		guidance:
			'Raise it if runs fail with out-of-memory errors partway through, or when another program shares the GPU. Lower values keep more of the model on the GPU.',
		defaultLabel: '1.0 GB',
		defaultValue: 1.0,
		min: 0,
		max: 64,
		step: 0.5,
		unit: 'GB'
	},
	{
		key: 'native_ltx_decode_tile_px',
		applies: 'live',
		group: 'memory',
		scope: 'backend',
		kind: 'int',
		label: 'LTX decode tile size',
		description: 'Size of the tiles LTX videos are decoded in. 0 picks the largest size that fits in free VRAM.',
		guidance: 'Set a smaller size if LTX decoding runs out of memory.',
		defaultLabel: '0 (automatic)',
		defaultValue: 0,
		min: 0,
		max: 4096,
		step: 32,
		unit: 'px'
	},
	{
		key: 'native_ltx_decode_tile_frames',
		applies: 'live',
		group: 'memory',
		scope: 'backend',
		kind: 'int',
		label: 'LTX decode tile length',
		description: 'Number of frames LTX videos are decoded in at a time. 0 picks it from free VRAM.',
		guidance: 'Set a smaller number if LTX decoding runs out of memory.',
		defaultLabel: '0 (automatic)',
		defaultValue: 0,
		min: 0,
		max: 1024,
		step: 8,
		unit: 'frames'
	},
	{
		key: 'profiling_enabled',
		applies: 'live',
		group: 'diagnostics',
		scope: 'app',
		kind: 'bool',
		label: 'Record a performance profile for each generation',
		description:
			"Each run writes a profile of where time and memory went: how long every stage took, RAM and VRAM sampled four times a second, model loads and evictions, LoRA events, and the app's log lines for that run.",
		details:
			'Profiles are saved in the file storage folder under profiles/<generation id>/ as profile.jsonl and generation.log. To read one, open the finished run in the workbench and choose Resource profile: it shows the report and lets you download both files. Only admins see it.',
		guidance:
			'Sampling adds a little overhead to every step. Turn it on while you look into a slow or memory-hungry run, then turn it off again.',
		defaultLabel: 'Off',
		defaultValue: false
	},
	{
		key: 'profiling_census',
		applies: 'live',
		group: 'diagnostics',
		scope: 'app',
		kind: 'bool',
		label: 'Include a memory census at the end of each run',
		description:
			'Lists every tensor still alive in RAM and VRAM after the run, grouped by what holds it, so a leak names its owner.',
		guidance:
			'The census takes a few seconds. It runs after the generation finishes, so results are not delayed. If another generation starts before it is done, the census for the earlier run is skipped.',
		defaultLabel: 'On',
		defaultValue: true,
		parent: 'profiling_enabled'
	},
	{
		key: 'native_sol_attn_debug',
		applies: 'live',
		group: 'debug',
		scope: 'backend',
		kind: 'bool',
		label: 'Log every Sol-Attn call',
		description: 'Writes the sequence length and routing of each Sol-Attn attention call to the log.',
		guidance: 'Only useful when debugging Sol-Attn. It makes the log very noisy.',
		defaultLabel: 'Off',
		defaultValue: false
	}
];

const BY_KEY = new Map(RUNTIME_SETTINGS.map((d) => [d.key, d]));

export function runtimeSetting(key: string): RuntimeSettingDescriptor | undefined {
	return BY_KEY.get(key);
}

export function runtimeSettingsFor(group: RuntimeSettingGroup): RuntimeSettingDescriptor[] {
	return RUNTIME_SETTINGS.filter((d) => d.group === group);
}

export function runtimeSettingKeys(scope: RuntimeSettingScope): string[] {
	return RUNTIME_SETTINGS.filter((d) => d.scope === scope).map((d) => d.key);
}

export function appliesLabel(applies: RuntimeSettingApplies): string {
	if (applies === 'next_load') return 'Applies on next model load';
	if (applies === 'restart') return 'Applies after restart';
	return '';
}

export function restartPendingKeys(keys: Iterable<string>): string[] {
	return [...new Set(keys)].filter((key) => BY_KEY.get(key)?.applies === 'restart');
}

export function runtimeSettingValue(
	descriptor: RuntimeSettingDescriptor,
	settings: Record<string, unknown>
): RuntimeSettingValue {
	const value = settings[descriptor.key];
	if (value === undefined || value === null) return descriptor.defaultValue;
	if (descriptor.kind === 'bool') return Boolean(value);
	if (descriptor.kind === 'choice') return String(value);
	const n = typeof value === 'string' ? Number(value) : value;
	return typeof n === 'number' && Number.isFinite(n) ? n : descriptor.defaultValue;
}

export function parseRuntimeNumber(descriptor: RuntimeSettingDescriptor, raw: string): number | null {
	if (raw.trim() === '') return null;
	const n = Number(raw);
	if (!Number.isFinite(n)) return null;
	return descriptor.kind === 'int' ? Math.round(n) : n;
}

export function changedKeysSince(
	keys: readonly string[],
	current: Record<string, unknown>,
	baseline: Record<string, unknown>
): string[] {
	return keys.filter((key) => JSON.stringify(current[key]) !== JSON.stringify(baseline[key]));
}
