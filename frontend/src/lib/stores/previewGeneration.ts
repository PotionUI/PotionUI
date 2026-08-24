import { logger } from '$lib/utils/logger';
import { writable, get } from 'svelte/store';
import { api, type Session } from '$lib/services/api/index';
import { storage } from '$lib/utils/storage';
import { WebSocketService, createGenerationSocket, type WebSocketMessage } from '$lib/services/websocket';
import { phrasebookStore } from '$lib/stores/phrasebook';
import type { RichSegment, Segment } from '$lib/types/segments';
import { flattenRichSegments, toEditorSegment, toRichSegment } from '$lib/utils/richSegments';

// Preview-generation feature (preset/session/mode selection, template, WS + polling
// status). Extracted verbatim from phrasebook/+page.svelte. Lives as a module-level
// store (not a component) so the WebSocket connection and any in-flight generation
// survive the Generate-Previews panel mounting/unmounting as the user switches
// between categories/values in the editor pane — only connect()/disconnect() (called
// from the page's onMount/onDestroy) affect the socket's lifetime.

const GENERATION_CONFIG_KEY = 'phrasebook-generation-config';

const DEFAULT_PROMPT_TEMPLATE = 'A photo of << value >>';

function defaultPromptSegments(): Segment[] {
	return [toEditorSegment({ type: 'content', content: DEFAULT_PROMPT_TEMPLATE, chips: {}, enabled: true })];
}

interface GenerationConfig {
	presetId: string;
	sessionId: string;
	mode: string;
	promptSegments: RichSegment[];
	negativePrompt: string;
	useFixedSeed: boolean;
	fixedSeed: number;
}

/** Config saved by a build that still had a flat `promptTemplate` string. */
interface LegacyGenerationConfig {
	promptTemplate?: string;
}

export interface PreviewGenerationState {
	presets: { id: string; name: string }[];
	sessions: Session[];
	modes: { name: string; label: string }[];
	selectedPresetId: string;
	selectedSessionId: string;
	selectedMode: string;
	promptSegments: Segment[];
	negativePrompt: string;
	useFixedSeed: boolean;
	fixedSeed: number;
	isGeneratingPreviews: boolean;
	previewGenerationStatus: string | null;
	generationConfigInitialized: boolean;
}

const initialState: PreviewGenerationState = {
	presets: [],
	sessions: [],
	modes: [],
	selectedPresetId: '',
	selectedSessionId: '',
	selectedMode: '',
	promptSegments: defaultPromptSegments(),
	negativePrompt: '',
	useFixedSeed: false,
	fixedSeed: 42,
	isGeneratingPreviews: false,
	previewGenerationStatus: null,
	generationConfigInitialized: false
};

// Module-scope (non-reactive) generation bookkeeping
let ws: WebSocketService | null = null;
let activeGenerationIds: Set<string> = new Set();
let completedCount = 0;
let totalGenerations = 0;
let pollingInterval: ReturnType<typeof setInterval> | null = null;

function createPreviewGenerationStore() {
	const { subscribe, set, update } = writable<PreviewGenerationState>(initialState);

	function state() {
		return get({ subscribe });
	}

	function saveGenerationConfig() {
		const s = state();
		if (!s.generationConfigInitialized) return;
		const config: GenerationConfig = {
			presetId: s.selectedPresetId,
			sessionId: s.selectedSessionId,
			mode: s.selectedMode,
			promptSegments: s.promptSegments.map(toRichSegment),
			negativePrompt: s.negativePrompt,
			useFixedSeed: s.useFixedSeed,
			fixedSeed: s.fixedSeed
		};
		storage.setJSON(GENERATION_CONFIG_KEY, config);
	}

	/** Reads a saved config, migrating a pre-segments `promptTemplate` string into a
	 *  single content segment so an older browser's local storage still restores.
	 *  Also rewrites any lingering `{{ value }}` token (the pre-rename placeholder)
	 *  to `<< value >>` in restored segment content. */
	function loadGenerationConfig(): GenerationConfig | null {
		const raw = storage.getJSON<GenerationConfig & LegacyGenerationConfig>(GENERATION_CONFIG_KEY);
		if (!raw) return null;
		if (Array.isArray(raw.promptSegments)) {
			return {
				...raw,
				promptSegments: raw.promptSegments.map((segment) => ({
					...segment,
					content: segment.content.replace(/\{\{\s*value\s*\}\}/g, '<< value >>')
				}))
			};
		}
		if (typeof raw.promptTemplate === 'string') {
			const content = raw.promptTemplate.replace(/\{\{\s*value\s*\}\}/g, '<< value >>');
			return {
				...raw,
				promptSegments: [{ type: 'content', content, chips: {}, enabled: true }]
			};
		}
		return { ...raw, promptSegments: [] };
	}

	function stopPolling() {
		if (pollingInterval) {
			clearInterval(pollingInterval);
			pollingInterval = null;
		}
	}

	function startPollingForUpdates(categoryId: string, selectedValueIds: Set<string>) {
		if (pollingInterval) return;

		let pollCount = 0;
		const maxPolls = 60;
		const selectedIdsAtStart = new Set(selectedValueIds);

		pollingInterval = setInterval(async () => {
			pollCount++;

			await phrasebookStore.loadCategoryValues(categoryId);
			const values = get(phrasebookStore).categoryValues[categoryId] || [];
			const selectedValuesWithPreviews = values.filter(
				(v) => selectedIdsAtStart.has(v.id) && v.preview_file_id
			).length;
			const totalSelected = selectedIdsAtStart.size;
			update((s) => ({
				...s,
				previewGenerationStatus: `Generating... ${selectedValuesWithPreviews}/${totalSelected} complete`
			}));

			if (selectedValuesWithPreviews >= totalSelected) {
				stopPolling();
				update((s) => ({
					...s,
					isGeneratingPreviews: false,
					previewGenerationStatus: `Completed ${totalSelected} preview generation${totalSelected > 1 ? 's' : ''}`
				}));
				return;
			}

			if (pollCount >= maxPolls) {
				stopPolling();
				update((s) => ({
					...s,
					isGeneratingPreviews: false,
					previewGenerationStatus: 'Generation may still be in progress - refresh to check'
				}));
			}
		}, 3000);
	}

	function handlePreviewGenerationMessage(message: WebSocketMessage, generationId: string, categoryId: string) {
		if (message.type === 'generation_complete') {
			completedCount++;
			update((s) => ({
				...s,
				previewGenerationStatus: `Generating previews... ${completedCount}/${totalGenerations} complete`
			}));

			ws?.unsubscribe(generationId);
			activeGenerationIds.delete(generationId);

			if (categoryId) {
				phrasebookStore.loadCategoryValues(categoryId);
			}

			if (activeGenerationIds.size === 0) {
				stopPolling();
				update((s) => ({
					...s,
					isGeneratingPreviews: false,
					previewGenerationStatus: `Completed ${completedCount} preview generation${completedCount > 1 ? 's' : ''}`
				}));
			}
		} else if (message.type === 'generation_error') {
			completedCount++;
			update((s) => ({
				...s,
				previewGenerationStatus: `Generating previews... ${completedCount}/${totalGenerations} (some failed)`
			}));

			ws?.unsubscribe(generationId);
			activeGenerationIds.delete(generationId);

			if (activeGenerationIds.size === 0) {
				stopPolling();
				update((s) => ({ ...s, isGeneratingPreviews: false, previewGenerationStatus: `Completed with some errors` }));
			}
		}
	}

	return {
		subscribe,

		connect() {
			ws = createGenerationSocket();
			ws.connect();
		},

		disconnect() {
			stopPolling();
			if (ws) {
				activeGenerationIds.forEach((id) => ws?.unsubscribe(id));
				ws.disconnect();
				ws = null;
			}
		},

		async loadPresets() {
			try {
				const response = await api.listPresets();
				if (response.success && response.data) {
					const presets = response.data.map((p) => ({ id: p.id, name: p.name }));
					update((s) => ({ ...s, presets }));

					const savedConfig = loadGenerationConfig();

					if (savedConfig && presets.some((p) => p.id === savedConfig.presetId)) {
						update((s) => ({
							...s,
							selectedPresetId: savedConfig.presetId,
							promptSegments: savedConfig.promptSegments?.length
								? savedConfig.promptSegments.map((segment) => toEditorSegment(segment))
								: defaultPromptSegments(),
							negativePrompt: savedConfig.negativePrompt || '',
							useFixedSeed: savedConfig.useFixedSeed ?? false,
							fixedSeed: savedConfig.fixedSeed ?? 42
						}));

						await this.loadSessionsForPreset(savedConfig.presetId);

						const s1 = state();
						if (savedConfig.sessionId && s1.sessions.some((sess) => sess.id === savedConfig.sessionId)) {
							update((s) => ({ ...s, selectedSessionId: savedConfig.sessionId }));
						}
						if (savedConfig.mode && s1.modes.some((m) => m.name === savedConfig.mode)) {
							update((s) => ({ ...s, selectedMode: savedConfig.mode }));
						}
					} else if (presets.length > 0) {
						update((s) => ({ ...s, selectedPresetId: presets[0].id }));
						await this.loadSessionsForPreset(presets[0].id);
					}

					update((s) => ({ ...s, generationConfigInitialized: true }));
				}
			} catch (error) {
				logger.error('Failed to load presets:', error);
			}
		},

		async loadSessionsForPreset(presetId: string) {
			update((s) => ({ ...s, sessions: [], selectedSessionId: '', modes: [], selectedMode: '' }));
			if (!presetId) return;

			try {
				const [sessionsResponse, modesResponse] = await Promise.all([
					api.getSessionsForPreset(presetId),
					api.getPresetModes(presetId)
				]);

				if (sessionsResponse.success && sessionsResponse.data) {
					const sessions = sessionsResponse.data;
					update((s) => ({
						...s,
						sessions,
						selectedSessionId: sessions.length > 0 ? sessions[0].id : ''
					}));
				}

				if (modesResponse.success && modesResponse.data) {
					const modes = modesResponse.data.modes;
					const defaultMode = modesResponse.data.default_mode;
					update((s) => ({
						...s,
						modes,
						selectedMode: defaultMode || (modes.length > 0 ? modes[0].name : '')
					}));
				}
			} catch (error) {
				logger.error('Failed to load sessions/modes:', error);
			}
		},

		async handlePresetChange() {
			await this.loadSessionsForPreset(state().selectedPresetId);
			saveGenerationConfig();
		},

		setSelectedPresetId(id: string) {
			update((s) => ({ ...s, selectedPresetId: id }));
			saveGenerationConfig();
		},

		setSelectedSessionId(id: string) {
			update((s) => ({ ...s, selectedSessionId: id }));
			saveGenerationConfig();
		},

		setSelectedMode(mode: string) {
			update((s) => ({ ...s, selectedMode: mode }));
			saveGenerationConfig();
		},

		setPromptSegments(segments: Segment[]) {
			update((s) => ({ ...s, promptSegments: segments }));
			saveGenerationConfig();
		},

		setNegativePrompt(value: string) {
			update((s) => ({ ...s, negativePrompt: value }));
			saveGenerationConfig();
		},

		setUseFixedSeed(value: boolean) {
			update((s) => ({ ...s, useFixedSeed: value }));
			saveGenerationConfig();
		},

		setFixedSeed(value: number) {
			update((s) => ({ ...s, fixedSeed: value }));
			saveGenerationConfig();
		},

		/**
		 * Drops every field back to its initial value, including the ones
		 * `saveGenerationConfig()` would otherwise persist (preset/session/mode,
		 * prompt template, negative prompt) — this store outlives the panel's own
		 * mount/unmount cycle by design (see the module header), so a different
		 * user signing in must not keep seeing the previous user's config in the
		 * bound inputs. Also tears down the socket/polling defensively, though in
		 * the normal flow the phrasebook page's own onDestroy already did that
		 * when the previous user's session ended.
		 */
		reset() {
			this.disconnect();
			activeGenerationIds = new Set();
			completedCount = 0;
			totalGenerations = 0;
			set(initialState);
		},

		async handleGeneratePreviews(categoryId: string, selectedValueIds: Set<string>) {
			const s0 = state();
			if (!categoryId || !s0.selectedSessionId || !s0.selectedMode) {
				update((s) => ({ ...s, previewGenerationStatus: 'Error: Please select a category, session, and mode' }));
				return;
			}

			if (selectedValueIds.size === 0) {
				update((s) => ({
					...s,
					previewGenerationStatus: 'Error: Please select at least one value to generate previews for'
				}));
				return;
			}

			const promptTemplate = flattenRichSegments(s0.promptSegments);
			if (!/<<\s*value\s*>>/.test(promptTemplate)) {
				update((s) => ({ ...s, previewGenerationStatus: 'Error: Prompt template must contain << value >> placeholder' }));
				return;
			}

			completedCount = 0;
			activeGenerationIds = new Set();
			update((s) => ({
				...s,
				isGeneratingPreviews: true,
				previewGenerationStatus: `Starting preview generation for ${selectedValueIds.size} value${selectedValueIds.size > 1 ? 's' : ''}... (this may take a while)`
			}));

			try {
				const response = await api.generatePreviews(categoryId, {
					session_id: s0.selectedSessionId,
					prompt_template: promptTemplate,
					mode: s0.selectedMode,
					negative_prompt: s0.negativePrompt || undefined,
					seed: s0.useFixedSeed ? s0.fixedSeed : undefined,
					value_ids: Array.from(selectedValueIds)
				});

				if (response.success && response.data) {
					totalGenerations = response.data.started || 0;
					update((s) => ({ ...s, previewGenerationStatus: `Generating previews... 0/${totalGenerations} complete` }));

					const generations = response.data.generations || [];
					if (ws && generations.length > 0) {
						generations.forEach((gen) => {
							if (gen.generation_id) {
								activeGenerationIds.add(gen.generation_id);
								ws!.subscribe(gen.generation_id, (message) => {
									handlePreviewGenerationMessage(message, gen.generation_id, categoryId);
								});
							}
						});
					}

					if (totalGenerations > 0) {
						startPollingForUpdates(categoryId, selectedValueIds);
					}

					if (totalGenerations === 0) {
						update((s) => ({ ...s, isGeneratingPreviews: false, previewGenerationStatus: 'No generations started' }));
					}
				} else {
					update((s) => ({
						...s,
						isGeneratingPreviews: false,
						previewGenerationStatus: response.error || 'Failed to start preview generation'
					}));
				}
			} catch (error) {
				logger.error('Failed to generate previews:', error);
				update((s) => ({
					...s,
					isGeneratingPreviews: false,
					previewGenerationStatus: `Error: ${error instanceof Error ? error.message : 'Failed to generate previews'}`
				}));
			}
		}
	};
}

export const previewGenerationStore = createPreviewGenerationStore();
