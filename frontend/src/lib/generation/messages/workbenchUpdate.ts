import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';

// Moved verbatim from generate/+page.svelte handleGenerationMessage's
// 'workbench_update' case (image/video/audio branching).
generationMessageRegistry.register('workbench_update', {
	type: 'workbench_update',
	handle(message: any, ctx) {
		const targetTabId = ctx.tabId;
		const targetTab = ctx.tab;

		const imageData = message.image;
		const videoPath = message.path;
		const isAudioUpdate = message.file_type === 'audio';
		const isVideoUpdate = message.file_type === 'video';
		// The unsaved-mesh case (metadata.processed === false in
		// MeshGenerationOutputHandler) carries file_type but no `path` at all -
		// requiring meshPath here means that message matches no branch below
		// and is silently ignored, rather than switching the panel to a mesh
		// view with nothing to show.
		const isMeshUpdate = message.file_type === 'mesh';
		const meshPath = isMeshUpdate ? message.path : undefined;

		if (isAudioUpdate && message.path) {
			// Handle audio updates
			const audioData = {
				url: message.path,
				originalUrl: message.path,
				track_type: message.track_type || 'speech',
				duration: message.duration,
				sample_rate: message.sample_rate,
				channels: message.channels,
				seed: message.seed,
				file_type: 'audio'
			};

			ctx.tabsStore.updateTab(targetTabId, {
				generation: {
					...targetTab.generation,
					currentGeneration: {
						...targetTab.generation.currentGeneration,
						current_audio: audioData,
						current_image: null,
						current_video: null,
						file_type: 'audio',
						status: targetTab.generation.currentGeneration?.status || 'running'
					}
				}
			});
		} else if (isVideoUpdate && videoPath) {
			// A final video update may also carry its last preview frame in `image`.
			// File type is authoritative: switch away from the preview image and mount
			// the video player as soon as the video path is available.
			const videoMetadata = {
				duration: message.duration,
				fps: message.fps,
				resolution: message.resolution,
				seed: message.seed,
				sampler: message.sampler,
				cfg: message.cfg,
				motion_strength: message.motion_strength
			};

			ctx.tabsStore.updateTab(targetTabId, {
				generation: {
					...targetTab.generation,
					currentGeneration: {
						...targetTab.generation.currentGeneration,
						current_video: videoPath,
						current_image: null,
						current_audio: null,
						file_type: 'video',
						video_metadata: videoMetadata,
						status: targetTab.generation.currentGeneration?.status || 'running'
					}
				}
			});
		} else if (isMeshUpdate && meshPath) {
			// Shaped after the video branch above, not the audio one: `current_mesh`
			// stays a bare URL (mirrors `current_video`) with geometry/identity
			// metadata split out into `mesh_metadata` (mirrors `video_metadata`) -
			// see `resolveMeshMetadata` in the mesh renderer for how both this and
			// the flat gallery-item shape get read back.
			const meshMetadata = {
				format: message.mesh_format,
				filename: message.mesh_name,
				vertex_count: message.vertex_count,
				face_count: message.face_count,
				seed: message.seed,
				temporary: message.temporary,
				derived: message.derived
			};

			ctx.tabsStore.updateTab(targetTabId, {
				generation: {
					...targetTab.generation,
					currentGeneration: {
						...targetTab.generation.currentGeneration,
						current_mesh: meshPath,
						current_image: null,
						current_video: null,
						current_audio: null,
						file_type: 'mesh',
						mesh_metadata: meshMetadata,
						status: targetTab.generation.currentGeneration?.status || 'running'
					}
				}
			});
		} else if (imageData) {
			// Process image data - handle base64 or URL
			let imageUrl: string;

			if (typeof imageData === 'string') {
				// Check if it's a URL or base64
				if (imageData.startsWith('/api/')) {
					// Relative API URL
					imageUrl = imageData;
				} else if (imageData.startsWith('http')) {
					// Absolute URL - convert to relative to avoid CORS
					imageUrl = imageData.replace(/^https?:\/\/[^\/]+/, '');
				} else if (imageData.startsWith('data:')) {
					// Data URL (base64)
					imageUrl = imageData;
				} else {
					// Raw base64 - add data: prefix
					imageUrl = `data:image/png;base64,${imageData}`;
				}
			} else {
				// Legacy format - convert to base64
				imageUrl = `data:image/png;base64,${imageData}`;
			}

			ctx.tabsStore.updateTab(targetTabId, {
				generation: {
					...targetTab.generation,
					currentGeneration: {
						...targetTab.generation.currentGeneration,
						current_image: imageUrl,
						current_video: null,
						current_audio: null,
						file_type: 'image',
						status: targetTab.generation.currentGeneration?.status || 'running'
					}
				}
			});
		} else if (videoPath && !isAudioUpdate && !isMeshUpdate) {
			// Handle video updates
			let videoUrl = videoPath;
			if (videoPath.startsWith('/api/')) {
				videoUrl = videoPath; // Keep relative URL
			}

			const videoMetadata = {
				duration: message.duration,
				fps: message.fps,
				resolution: message.resolution,
				seed: message.seed,
				sampler: message.sampler,
				cfg: message.cfg,
				motion_strength: message.motion_strength
			};

			ctx.tabsStore.updateTab(targetTabId, {
				generation: {
					...targetTab.generation,
					currentGeneration: {
						...targetTab.generation.currentGeneration,
						current_video: videoUrl,
						current_image: null,
						current_audio: null,
						file_type: 'video',
						video_metadata: videoMetadata,
						status: targetTab.generation.currentGeneration?.status || 'running'
					}
				}
			});
		}
	}
});
