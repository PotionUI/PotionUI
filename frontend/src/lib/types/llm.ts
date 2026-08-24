export interface LLMConfig {
	id: string;
	name: string;
	type: 'ollama' | 'openai';
	enabled: boolean;
	base_url: string;
	/** Whether a key is stored server-side. The key itself is never returned. */
	api_key_set?: boolean;
	model: string;
	system_message: string;
	temperature: number;
	max_tokens: number;
	timeout: number;
	supports_vision?: boolean;
	/** Extracts durable user facts from the transcript into memory notes. Default true. */
	memory_reflection?: boolean;
	is_default?: boolean;
	provider_options?: OllamaOptions | OpenAIOptions | Record<string, unknown>;
}

/**
 * Ollama-specific provider options
 */
export interface OllamaOptions {
	// Model loading
	keep_alive?: string | number;  // How long to keep model in memory (e.g., "5m", "1h", -1 for indefinite, 0 to unload immediately)
	num_gpu?: number;              // Number of GPU layers to use
	num_thread?: number;           // Number of CPU threads to use

	// Context
	num_ctx?: number;              // Context window size (default: 2048)
	num_batch?: number;            // Batch size for prompt processing
	num_keep?: number;             // Tokens to keep from initial prompt

	// Sampling
	seed?: number;                 // Random seed for reproducibility
	top_k?: number;                // Top-k sampling (default: 40)
	top_p?: number;                // Nucleus sampling (default: 0.9)
	min_p?: number;                // Min-p sampling
	tfs_z?: number;                // Tail-free sampling (default: 1.0)
	typical_p?: number;            // Typical p sampling (default: 1.0)

	// Repetition
	repeat_penalty?: number;       // Repetition penalty (default: 1.1)
	repeat_last_n?: number;        // Tokens to look back for repeat penalty (default: 64)
	presence_penalty?: number;     // Presence penalty (default: 0.0)
	frequency_penalty?: number;    // Frequency penalty (default: 0.0)

	// Mirostat
	mirostat?: number;             // Mirostat mode (0=disabled, 1=v1, 2=v2)
	mirostat_tau?: number;         // Mirostat target entropy (default: 5.0)
	mirostat_eta?: number;         // Mirostat learning rate (default: 0.1)

	// Other
	stop?: string[];               // Stop sequences

	// Tool calling
	force_prompt_tools?: boolean;  // Inject tool schemas into system prompt instead of native tool calling (for models like Gemma 4 where native tools don't work)
}

/**
 * OpenAI-specific provider options
 */
export interface OpenAIOptions {
	top_p?: number;                // Nucleus sampling
	frequency_penalty?: number;    // Frequency penalty (-2.0 to 2.0)
	presence_penalty?: number;     // Presence penalty (-2.0 to 2.0)
	seed?: number;                 // Random seed for deterministic output
	stop?: string[];               // Stop sequences (up to 4)
	logit_bias?: Record<string, number>;  // Token bias
}

/**
 * Pre-chat action that can run before LLM invocation
 */
export interface PreChatAction {
	id: string;
	name: string;
	description: string;
	plugin_id: string;
	default_enabled: boolean;
	blocking: boolean;
	category: string;
}

/**
 * Every registered chat tool merged with its admin governance row (admin
 * Toolset tab). A tool with no governance row reports enabled=true,
 * locked=false — the zero-behavior-change default.
 */
export interface AdminToolsetEntry {
	name: string;
	label: string;
	group: string;
	user_description: string;
	requires_approval: boolean;
	enabled: boolean;
	locked: boolean;
	source: string;
}

/**
 * A tool the current user may see and toggle. Admin-disabled tools are
 * omitted from this list entirely, not shown as unavailable.
 */
export interface UserToolPreference {
	name: string;
	label: string;
	user_description: string;
	enabled_by_admin: boolean;
	locked: boolean;
	disabled_by_user: boolean;
}
