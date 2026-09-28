/**
 * Static structure for the System Settings master-detail rebuild: the
 * sections shown in the left rail, and which `userConfigurableSettings` key
 * (the PUT body System SettingsTab sends) belongs to which section — this is
 * what drives per-group dirty tracking off one snapshot diff.
 */

export type SettingsGroupId =
	| 'access'
	| 'content_safety'
	| 'media_storage'
	| 'backups'
	| 'housekeeping'
	| 'search_tagging'
	| 'generation'
	| 'external_login'
	| 'logs';

export interface SettingsGroupDescriptor {
	id: SettingsGroupId;
	label: string;
	icon: string;
}

export const SETTINGS_GROUPS: SettingsGroupDescriptor[] = [
	{ id: 'access', label: 'Access', icon: 'group' },
	{ id: 'content_safety', label: 'Content Safety', icon: 'shield' },
	{ id: 'media_storage', label: 'Media storage', icon: 'photo' },
	{ id: 'backups', label: 'Backups', icon: 'save' },
	{ id: 'housekeeping', label: 'Housekeeping', icon: 'trash' },
	{ id: 'search_tagging', label: 'Search & Tagging', icon: 'search' },
	{ id: 'generation', label: 'Generation', icon: 'image' },
	{ id: 'external_login', label: 'External Login', icon: 'globe' },
	{ id: 'logs', label: 'Logs', icon: 'clipboard-list' }
];

const LEGACY_SETTINGS_GROUP_ALIASES: Record<string, SettingsGroupId> = {
	storage: 'media_storage'
};

export function resolveSettingsGroupId(requested: string | null): SettingsGroupId {
	const direct = SETTINGS_GROUPS.find((g) => g.id === requested)?.id;
	if (direct) return direct;
	if (requested && LEGACY_SETTINGS_GROUP_ALIASES[requested]) return LEGACY_SETTINGS_GROUP_ALIASES[requested];
	return 'access';
}

export function settingsGroupHasFooter(id: SettingsGroupId): boolean {
	return id !== 'logs';
}

export function computeDirtyGroups(dirtyKeys: readonly string[]): Set<SettingsGroupId> {
	return new Set(
		dirtyKeys
			.map((key) => SETTINGS_KEY_GROUP[key])
			.filter((group): group is SettingsGroupId => group !== undefined)
	);
}

export const SETTINGS_KEY_GROUP: Record<string, SettingsGroupId> = {
	file_storage_directory: 'media_storage',
	storage_backend: 'media_storage',
	s3_bucket: 'media_storage',
	s3_prefix: 'media_storage',
	s3_endpoint_url: 'media_storage',
	s3_region: 'media_storage',
	s3_access_key_id: 'media_storage',
	s3_secret_key: 'media_storage',
	s3_path_style: 'media_storage',
	thumbnail_sizes: 'media_storage',
	thumbnail_video_fps: 'media_storage',
	thumbnail_video_seconds: 'media_storage',
	thumbnail_video_quality: 'media_storage',
	thumbnail_image_quality: 'media_storage',
	tmp_retention_days: 'housekeeping',
	run_report_retention_days: 'housekeeping',
	llm_trace_retention_days: 'housekeeping',
	backup_destination: 'backups',
	backup_retention: 'backups',
	backup_default_tier: 'backups',
	nsfw: 'content_safety',
	prompt_embedding_provider: 'search_tagging',
	prompt_embedding_model: 'search_tagging',
	prompt_embedding_device: 'search_tagging',
	prompt_embedding_auto_download: 'search_tagging',
	prompt_embedding_ollama_base_url: 'search_tagging',
	prompt_embedding_ollama_model: 'search_tagging',
	registration_policy: 'access',
	media_tagger_model: 'search_tagging',
	media_tagger_device: 'search_tagging',
	media_tagger_auto_download: 'search_tagging',
	media_tagger_tag_threshold: 'search_tagging',
	media_tagger_character_threshold: 'search_tagging',
	media_nsfw_blur_threshold: 'content_safety',
	media_vision_model: 'search_tagging',
	media_vision_device: 'search_tagging',
	media_vision_auto_download: 'search_tagging',
	mcp_enabled: 'access',
	workbench_single_result_gallery: 'generation',
	notify_admins_on_generation_failure: 'generation',
	notify_admins_on_generation_failure_categories: 'generation',
	external_login_auto_create: 'external_login',
	external_login_link_by_email: 'external_login',
	external_login_default_group: 'external_login'
};
