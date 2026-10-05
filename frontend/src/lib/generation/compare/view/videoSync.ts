export interface VideoLike {
	currentTime: number;
	duration: number;
	paused: boolean;
	muted: boolean;
	loop: boolean;
	play(): Promise<void> | void;
	pause(): void;
}

export const SEEK_TOLERANCE = 0.12;

export function finiteDuration(video: Pick<VideoLike, 'duration'>): number {
	return Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 0;
}

export function longestDuration(videos: Iterable<Pick<VideoLike, 'duration'>>): number {
	let longest = 0;
	for (const video of videos) longest = Math.max(longest, finiteDuration(video));
	return longest;
}

export function targetTime(globalTime: number, clipDuration: number): number {
	if (clipDuration <= 0) return 0;
	return Math.min(Math.max(0, globalTime), clipDuration);
}

export function needsSeek(current: number, target: number, tolerance = SEEK_TOLERANCE): boolean {
	return Math.abs(current - target) > tolerance;
}

export function formatClock(seconds: number): string {
	const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
	const totalTenths = Math.floor(safe * 10 + 1e-6);
	const whole = Math.floor(totalTenths / 10);
	const minutes = Math.floor(whole / 60);
	return `${String(minutes).padStart(2, '0')}:${String(whole % 60).padStart(2, '0')}.${totalTenths % 10}`;
}

export interface VideoGroupState {
	playing: boolean;
	muted: boolean;
	synced: boolean;
	time: number;
	duration: number;
}

export interface VideoGroup {
	add(video: VideoLike): void;
	remove(video: VideoLike): void;
	setVisible(video: VideoLike, visible: boolean): void;
	play(): void;
	pause(): void;
	toggle(): void;
	seek(seconds: number): void;
	setMuted(muted: boolean): void;
	setSynced(synced: boolean): void;
	tick(): void;
	ended(video: VideoLike): void;
	state(): VideoGroupState;
}

function leaderOf(videos: Iterable<VideoLike>): VideoLike | null {
	let leader: VideoLike | null = null;
	let longest = 0;
	for (const video of videos) {
		const duration = finiteDuration(video);
		if (duration > longest) {
			longest = duration;
			leader = video;
		}
	}
	return leader;
}

export function createVideoGroup(): VideoGroup {
	const videos = new Set<VideoLike>();
	const hidden = new Set<VideoLike>();
	let playing = true;
	let muted = true;
	let synced = true;

	function run(video: VideoLike) {
		const result = video.play();
		if (result && typeof (result as Promise<void>).catch === 'function') {
			(result as Promise<void>).catch(() => undefined);
		}
	}

	function applyPlayback() {
		for (const video of videos) {
			video.muted = muted;
			video.loop = !synced;
			if (playing && !hidden.has(video)) run(video);
			else video.pause();
		}
	}

	function seekAll(seconds: number) {
		for (const video of videos) {
			video.currentTime = synced ? targetTime(seconds, finiteDuration(video)) : seconds;
		}
	}

	return {
		add(video) {
			videos.add(video);
			video.muted = muted;
			video.loop = !synced;
			if (playing) run(video);
		},
		remove(video) {
			videos.delete(video);
			hidden.delete(video);
		},
		setVisible(video, visible) {
			if (visible) hidden.delete(video);
			else hidden.add(video);
			if (!videos.has(video)) return;
			if (visible && playing) run(video);
			else if (!visible) video.pause();
		},
		play() {
			playing = true;
			applyPlayback();
		},
		pause() {
			playing = false;
			applyPlayback();
		},
		toggle() {
			if (playing) this.pause();
			else this.play();
		},
		seek(seconds) {
			seekAll(seconds);
		},
		setMuted(next) {
			muted = next;
			for (const video of videos) video.muted = next;
		},
		setSynced(next) {
			synced = next;
			const leader = leaderOf(videos);
			for (const video of videos) video.loop = !next;
			if (next && leader) seekAll(leader.currentTime);
		},
		tick() {
			if (!synced) return;
			const leader = leaderOf(videos);
			if (!leader) return;
			for (const video of videos) {
				if (video === leader) continue;
				const target = targetTime(leader.currentTime, finiteDuration(video));
				if (needsSeek(video.currentTime, target)) video.currentTime = target;
			}
		},
		ended(video) {
			if (!synced || leaderOf(videos) !== video) return;
			seekAll(0);
			if (playing) for (const video of videos) if (!hidden.has(video)) run(video);
		},
		state() {
			const leader = leaderOf(videos);
			return {
				playing,
				muted,
				synced,
				time: leader ? leader.currentTime : 0,
				duration: longestDuration(videos)
			};
		}
	};
}
