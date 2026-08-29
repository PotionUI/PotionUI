/**
 * Static structure for the System Settings master-detail rebuild: the
 * sections shown in the left rail, and which `userConfigurableSettings` key
 * (the PUT body System SettingsTab sends) belongs to which section — this is
 * what drives per-group dirty tracking off one snapshot diff.
 */

export type SettingsGroupId = 'access' | 'content_safety' | 'storage' | 'search_tagging';

export interface SettingsGroupDescriptor {
	id: SettingsGroupId;
	label: string;
	icon: string;
}

export const SETTINGS_GROUPS: SettingsGroupDescriptor[] = [
	{ id: 'access', label: 'Access', icon: 'group' },
	{ id: 'content_safety', label: 'Content Safety', icon: 'shield' },
	{ id: 'storage', label: 'Storage', icon: 'folder' },
	{ id: 'search_tagging', label: 'Search & Tagging', icon: 'search' }
];

/** Storage's S3 fields and Models Location manage their own apply flow (see
 * `FileStoragePanel`/`ModelsLocationPanel`) and are deliberately absent here
 * — they never ride the shared save bar. */
export const SETTINGS_KEY_GROUP: Record<string, SettingsGroupId> = {
	file_storage_directory: 'storage',
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
	mcp_enabled: 'access'
};
