import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';

// Gallery updates can contain any combination of images, videos, and audio.
generationMessageRegistry.register('gallery_update', {
	type: 'gallery_update',
		handle(message: any, ctx) {
		const targetTabId = ctx.tabId;
		const targetTab = ctx.tab;
		let nextImages = targetTab.generation.batchImages || [];
		let nextVideos = targetTab.generation.batchVideos || [];
		let nextAudios = targetTab.generation.batchAudios || [];
		let nextMeshes = targetTab.generation.batchMeshes || [];

		// The message has 'images' array (base64 data) AND 'image_urls_list' (metadata)
		if (message.images && Array.isArray(message.images)) {
			nextImages = message.images.map((img: any, index: number) => {
				// Process image data (could be base64 or URL)
				let imageUrl: string;
				if (typeof img === 'string') {
					if (img.startsWith('/api/')) {
						imageUrl = img;
					} else if (img.startsWith('http')) {
						imageUrl = img.replace(/^https?:\/\/[^\/]+/, '');
					} else if (img.startsWith('data:')) {
						imageUrl = img;
					} else {
						// Raw base64
						imageUrl = `data:image/png;base64,${img}`;
					}
				} else {
					imageUrl = `data:image/png;base64,${img}`;
				}

				// Get metadata from image_urls_list if available
				let originalUrl = imageUrl;
				let derived = false;
				let seed, resolution, sampler, clip_skip, cfg, denoise, step;

				if (message.image_urls_list && message.image_urls_list[index]) {
					const imageUrls = message.image_urls_list[index];
					if (imageUrls.original) {
						originalUrl = imageUrls.original;
					} else if (imageUrls.path) {
						originalUrl = imageUrls.path;
					}

					derived = imageUrls.derived === true;
					seed = imageUrls.seed;
					sampler = imageUrls.sampler;
					clip_skip = imageUrls.clip_skip;
					cfg = imageUrls.cfg;
					denoise = imageUrls.denoise;
					step = imageUrls.step;

					// Parse resolution
					if (imageUrls.resolution && typeof imageUrls.resolution === 'string') {
						const [width, height] = imageUrls.resolution.split('x').map(Number);
						if (!isNaN(width) && !isNaN(height)) {
							resolution = [width, height];
						}
					}
				}

				// Fallback: construct API URL if needed
				if (originalUrl === imageUrl && message.generation_id) {
					originalUrl = `/api/media/generations/${message.generation_id}/${index}.png`;
				}

				return {
					url: imageUrl,
					originalUrl: originalUrl,
					derived,
					seed,
					resolution,
					sampler,
					clip_skip,
					cfg,
					denoise,
					step
				};
			});

		}

		// Handle final videos. The serializer provides the playable API path on
		// `video_urls_list`; `videos` carries the same item metadata but may omit
		// its path for temporary entries.
		if (message.videos && Array.isArray(message.videos)) {
			nextVideos = message.videos
				.map((video: any, index: number) => {
					const urlData = message.video_urls_list?.[index] || {};
					const path = urlData.path || video.path || '';
					if (!path) return null;
					return {
						url: path,
						originalUrl: path,
						file_type: 'video',
						derived: (urlData.derived ?? video.derived) === true,
						duration: urlData.duration ?? video.duration,
						fps: urlData.fps ?? video.fps,
						resolution: urlData.resolution ?? video.resolution,
						seed: urlData.seed ?? video.seed,
						sampler: urlData.sampler ?? video.sampler,
						clip_skip: urlData.clip_skip ?? video.clip_skip,
						cfg: urlData.cfg ?? video.cfg,
						denoise: urlData.denoise ?? video.denoise,
						step: urlData.step ?? video.step,
						motion_strength: urlData.motion_strength ?? video.motion_strength
					};
				})
				.filter(Boolean);

		}

		// Handle audio files in gallery update
		if (message.audios && Array.isArray(message.audios)) {
			nextAudios = message.audios.map((audio: any, index: number) => {
				// Get metadata from audio_urls_list if available
				let audioUrl = audio.path || '';
				let originalUrl = audioUrl;

				if (message.audio_urls_list && message.audio_urls_list[index]) {
					const audioUrls = message.audio_urls_list[index];
					if (audioUrls.path) {
						originalUrl = audioUrls.path;
						audioUrl = audioUrls.path;
					}
				}

				return {
					url: audioUrl,
					originalUrl: originalUrl,
					track_type: audio.track_type || 'speech',
					duration: audio.duration,
					sample_rate: audio.sample_rate,
					channels: audio.channels,
					seed: audio.seed,
					file_type: 'audio'
				};
			});

		}

		// Final meshes. Same shape as the video branch: `mesh_urls_list` carries
		// the servable path, `meshes` the item metadata - a temporary mesh has no
		// path and is skipped rather than rendered as an empty viewer.
		if (message.meshes && Array.isArray(message.meshes)) {
			nextMeshes = message.meshes
				.map((mesh: any, index: number) => {
					const urlData = message.mesh_urls_list?.[index] || {};
					const path = urlData.path || mesh.path || '';
					if (!path) return null;
					return {
						url: path,
						originalUrl: path,
						file_type: 'mesh',
						mesh_format: urlData.mesh_format ?? mesh.mesh_format ?? 'glb',
						derived: (urlData.derived ?? mesh.derived) === true,
						vertex_count: urlData.vertex_count ?? mesh.vertex_count,
						face_count: urlData.face_count ?? mesh.face_count,
						seed: urlData.seed ?? mesh.seed
					};
				})
				.filter(Boolean);
		}

		ctx.tabsStore.updateTab(targetTabId, {
			generation: {
				...targetTab.generation,
				batchImages: nextImages,
				batchVideos: nextVideos,
				batchAudios: nextAudios,
				batchMeshes: nextMeshes,
				workbenchTotal: nextImages.length + nextVideos.length + nextAudios.length + nextMeshes.length
			}
		});
	}
});
