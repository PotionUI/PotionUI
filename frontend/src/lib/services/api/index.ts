import { APIClient } from './client';

// Re-export all types so existing imports continue to work
export type {
	APIResponse,
	PromptPair,
	SegmentInput,
	SegmentPhrasebookInput,
	GenerationSegment,
	GenerationRequest,
	GenerationStatus,
	PresetInfo,
	PresetModeVariant,
	PresetModeInfo,
	PresetConfigurationEntry,
	PresetConfigurationResponse,
	PresetFormOverrideOption,
	PresetFormOverridePatch,
	PresetFormOverrideField,
	PresetFormOverridesResponse,
	TagUsageRef,
	ModelRecommendation,
	ModelRecommendationSource,
	ModelDownloadStatus,
	PromptTabSessionData,
	SessionData,
	ModeBasedSessionData,
	Session,
	SaveSessionRequest,
	UpdateSessionRequest,
	SessionVersionSummary,
	SessionVersionDetail,
	PhrasebookStateFilter,
	PhrasebookCategory,
	PhrasebookValue,
	PhrasebookSearchResult,
	GeneratePreviewRequest,
	GeneratePreviewResult,
	ChatMessageResponse,
	ChatSessionResponse,
	ChatSessionWithMessagesResponse,
	SendChatMessageResponse,
	ToolExecution,
	DocItem,
	DocSection,
	DocTree,
	DocContent
} from '$lib/types/api';
import { createPresetsApi } from './presets';
import { createGenerationsApi } from './generations';
import { createCollectionsApi } from './collections';
import { createSessionsApi } from './sessions';
import { createModelsApi } from './models';
import { createStatsApi } from './stats';
import { createModelCollectionsApi } from './modelCollections';
import { createPhrasebookApi } from './phrasebook';
import { createChatApi } from './chat';
import { createLlmApi } from './llm';
import { createMediaApi } from './media';
import { createLibraryApi } from './library';
import { createPromptsApi } from './prompts';
import { createSegmentsApi } from './segments';
import { createDeveloperApi } from './developer';
import { createDocsApi } from './docs';
import { createPipesApi } from './pipes';
import { createNotificationsApi } from './notifications';
import { createAutomationsApi } from './automations';
import { createSetupApi } from './setup';
import { createMcpApi } from './mcp';
import { createInspirationsApi } from './inspirations';

const apiClient = new APIClient();

export const api = {
	// Auth + client methods
	login: apiClient.login.bind(apiClient),
	register: apiClient.register.bind(apiClient),
	getCurrentUser: apiClient.getCurrentUser.bind(apiClient),
	changePassword: apiClient.changePassword.bind(apiClient),
	uploadAvatar: apiClient.uploadAvatar.bind(apiClient),
	deleteAvatar: apiClient.deleteAvatar.bind(apiClient),
	setAuthHeader: apiClient.setAuthHeader.bind(apiClient),
	clearAuth: apiClient.clearAuth.bind(apiClient),
	getToken: apiClient.getToken.bind(apiClient),
	getBaseURL: apiClient.getBaseURL.bind(apiClient),
	getClient: apiClient.getClient.bind(apiClient),
	setOnAuthExpired: apiClient.setOnAuthExpired.bind(apiClient),

	// Domain modules
	...createPresetsApi(apiClient.getClient()),
	...createGenerationsApi(apiClient.getClient()),
	...createCollectionsApi(apiClient.getClient()),
	...createSessionsApi(apiClient.getClient()),
	...createModelsApi(apiClient.getClient()),
	...createStatsApi(apiClient.getClient()),
	...createModelCollectionsApi(apiClient.getClient()),
	...createPhrasebookApi(apiClient.getClient()),
	...createChatApi(
		apiClient.getClient(),
		apiClient.getToken.bind(apiClient),
		apiClient.getBaseURL.bind(apiClient),
		apiClient.triggerAuthExpired.bind(apiClient)
	),
	...createLlmApi(apiClient.getClient()),
	...createMediaApi(apiClient.getClient(), apiClient.getBaseURL.bind(apiClient)),
	...createLibraryApi(apiClient.getClient()),
	...createPromptsApi(apiClient.getClient()),
	...createSegmentsApi(apiClient.getClient()),
	...createDeveloperApi(apiClient.getClient()),
	...createDocsApi(apiClient.getClient()),
	...createPipesApi(apiClient.getClient()),
	...createNotificationsApi(apiClient.getClient()),
	...createAutomationsApi(apiClient.getClient()),
	...createSetupApi(apiClient.getClient()),
	...createMcpApi(apiClient.getClient()),
	...createInspirationsApi(apiClient.getClient())
};

export type { APIClient };
export type {
	SetupStatus,
	ReadinessArea,
	ReadinessStatus,
	ReadinessCheck,
	ReadinessReport,
	SetupRunStatus,
	SetupStepStatus,
	SetupRunAction,
	SetupStepAttempt,
	SetupRunStepView,
	SetupRun,
	SetupRecipe,
	SetupConsentArtifact,
	SetupConsentRequest
} from './setup';
export type { McpToken, McpTokenCreated, McpStatus } from './mcp';
export type {
	InspirationAuthor,
	InspirationMedia,
	InspirationParamPreview,
	InspirationDto,
	InspirationsListResult,
	InspirationsListQuery,
	InspirationParamsResult,
	InspirationComment,
	InspirationCollection
} from './inspirations';
