/**
 * Static structure for the System Settings master-detail rebuild: the
 * sections shown in the left rail, and which `userConfigurableSettings` key
 * (the PUT body System SettingsTab sends) belongs to which section — this is
 * what drives per-group dirty tracking off one snapshot diff.
 */

export type SettingsGroupId =
	| 'access'
	| 'content_safety'
	| 'file_storage'
	| 'models_location'
	| 'prompt_search'
	| 'media_tagging'
	| 'visual_search';

export interface SettingsGroupDescriptor {
	id: SettingsGroupId;
	label: string;
	icon: string;
}

export const SETTINGS_GROUPS: SettingsGroupDescriptor[] = [
	{ id: 'access', label: 'Access', icon: 'group' },
	{ id: 'content_safety', label: 'Content Safety', icon: 'shield' },
	{ id: 'file_storage', label: 'File Storage', icon: 'folder' },
	{ id: 'models_location', label: 'Models Location', icon: 'cube' },
	{ id: 'prompt_search', label: 'Prompt Search', icon: 'search' },
	{ id: 'media_tagging', label: 'Media Tagging', icon: 'tag' },
	{ id: 'visual_search', label: 'Visual Search', icon: 'photo' }
];

/** File Storage's S3 fields and Models Location manage their own apply flow
 * (see `FileStoragePanel`/`ModelsLocationPanel`) and are deliberately absent
 * here — they never ride the shared save bar. */
export const SETTINGS_KEY_GROUP: Record<string, SettingsGroupId> = {
	file_storage_directory: 'file_storage',
	nsfw: 'content_safety',
	prompt_embedding_provider: 'prompt_search',
	prompt_embedding_model: 'prompt_search',
	prompt_embedding_device: 'prompt_search',
	prompt_embedding_auto_download: 'prompt_search',
	prompt_embedding_ollama_base_url: 'prompt_search',
	prompt_embedding_ollama_model: 'prompt_search',
	registration_policy: 'access',
	media_tagger_model: 'media_tagging',
	media_tagger_device: 'media_tagging',
	media_tagger_auto_download: 'media_tagging',
	media_tagger_tag_threshold: 'media_tagging',
	media_tagger_character_threshold: 'media_tagging',
	media_nsfw_blur_threshold: 'content_safety',
	media_vision_model: 'visual_search',
	media_vision_device: 'visual_search',
	media_vision_auto_download: 'visual_search',
	mcp_enabled: 'access'
};
