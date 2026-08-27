"""Read side of generation history.

Every method here is a query or a validation - it looks generations up, lists
them with filters, or checks ownership - and none of them mutate a generation or
touch the filesystem. Writes and file IO live in GenerationHistoryArchive.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING

from src.features.generation.exceptions import (
    GenerationNotFoundException,
    InvalidTagException,
    InvalidDateFilterException,
)
from src.features.generation import profile_paths
from src.features.generation.records import Generation
from src.features.generation.repository import GenerationRepository

if TYPE_CHECKING:
    from src.platform.filesystem import FileStore
    from src.platform.settings.settings import Settings
    from src.features.media_index.indexer import MediaIndexer
    from src.features.media_index.repository import MediaIndexRepository
    from src.features.presets.name_resolver import PresetNameResolver

logger = logging.getLogger(__name__)


class GenerationHistoryQuery:
    """Read-side of the generation history: listing, lookup, facets, validation."""

    # Provenance chains (enhance-of-enhance-of-...): a hard stop so a
    # pathological or (despite the cycle guard) unexpectedly circular chain
    # can never turn one get_params call into unbounded recursion.
    _MAX_PROVENANCE_DEPTH = 5

    def __init__(
        self,
        generation_repo: GenerationRepository,
        file_service: Optional["FileStore"] = None,
        media_index_repository: Optional["MediaIndexRepository"] = None,
        settings: Optional["Settings"] = None,
        media_indexer: Optional["MediaIndexer"] = None,
        preset_name_resolver: Optional["PresetNameResolver"] = None,
    ):
        """Initialize GenerationHistoryQuery.

        Args:
            generation_repo: Repository for generation data access
            file_service: File store, used only to locate per-generation profiler
                artifacts (its ``base_storage_dir`` is the same root the profiler
                writes under, so ``has_profile`` and the profile download endpoint
                agree). Optional so read paths that never touch profiles work
                without it.
            media_index_repository: Source of auto-tagger system tags and rating
                scores attached to each file in history payloads. Optional so
                read paths that never surface them work without it.
            settings: Resolves the NSFW blur threshold so each file's
                ``nsfw`` flag is decided server-side (the threshold is a SYSTEM
                setting regular users cannot read). Optional; defaults apply.
            media_indexer: Backs semantic (visual) history search via
                its gallery vector store. Optional; ``semantic_query`` returns
                no results when absent.
            preset_name_resolver: Resolves preset ids to their YAML display
                names. Optional; ids are shown verbatim when absent.
        """
        self.generation_repo = generation_repo
        self.file_service = file_service
        self.media_index_repository = media_index_repository
        self.settings = settings
        self.media_indexer = media_indexer
        self.preset_name_resolver = preset_name_resolver

    def _preset_name_map(self) -> Dict[str, str]:
        """One id -> name snapshot per serialization pass; never per row."""
        if self.preset_name_resolver is None:
            return {}
        try:
            return self.preset_name_resolver.name_map()
        except Exception:
            logger.exception("preset name resolution failed; falling back to ids")
            return {}

    def _get_generation_or_raise(
        self, generation_id: str, user_id: str, include_files: bool = False
    ) -> Generation:
        """Get generation by ID or raise GenerationNotFoundException.

        Args:
            generation_id: The generation ID to look up
            user_id: The user ID for ownership verification
            include_files: Whether to include file records

        Returns:
            Generation if found

        Raises:
            GenerationNotFoundException: If generation not found
        """
        generation = self.generation_repo.get_by_id(
            generation_id, user_id=user_id, include_files=include_files
        )
        if not generation:
            raise GenerationNotFoundException(f"Generation '{generation_id}' not found")
        return generation

    def _validate_tag_ids(self, tag_ids: List[str], user_id: str) -> None:
        """Validate that all tag IDs are valid GENERATION type tags owned by user.

        Args:
            tag_ids: List of tag IDs to validate
            user_id: The user ID for ownership verification

        Raises:
            InvalidTagException: If any tag is invalid
        """
        from src.features.tags.repository import tag_repo

        for tag_id in tag_ids:
            tag = tag_repo.get_tag_by_id(tag_id)
            if not tag or tag.type != 'GENERATION' or tag.user_id != user_id:
                raise InvalidTagException(f"Invalid tag ID: {tag_id}")

    def validate_date_filters(
        self,
        created_from: Optional[str],
        created_to: Optional[str],
        completed_from: Optional[str],
        completed_to: Optional[str]
    ) -> None:
        """Validate date filter parameters.

        Args:
            created_from: Start date for creation filter
            created_to: End date for creation filter
            completed_from: Start date for completion filter
            completed_to: End date for completion filter

        Raises:
            InvalidDateFilterException: If any date format is invalid or range is invalid
        """
        def parse_date(date_str: str, param_name: str) -> None:
            """Parse and validate a date string."""
            if not date_str:
                return

            try:
                # Try parsing as date only (YYYY-MM-DD)
                try:
                    datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    # Try parsing as datetime (YYYY-MM-DD HH:MM:SS)
                    datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                raise InvalidDateFilterException(
                    f"Invalid date format for {param_name}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
                )

        # Validate each date parameter
        for date_str, param_name in [
            (created_from, "created_from"),
            (created_to, "created_to"),
            (completed_from, "completed_from"),
            (completed_to, "completed_to")
        ]:
            parse_date(date_str, param_name)

        # Validate date ranges
        def compare_dates(from_date: Optional[str], to_date: Optional[str], prefix: str) -> None:
            if from_date and to_date:
                try:
                    from_dt = datetime.fromisoformat(from_date.replace(' ', 'T'))
                    to_dt = datetime.fromisoformat(to_date.replace(' ', 'T'))

                    if from_dt > to_dt:
                        raise InvalidDateFilterException(
                            f"{prefix}_from date must be before {prefix}_to date"
                        )
                except ValueError:
                    pass  # Date format validation already handled above

        # Check date ranges
        compare_dates(created_from, created_to, "created")
        compare_dates(completed_from, completed_to, "completed")

    def get_history(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = 50,
        offset: int = 0,
        status: Optional[str] = None,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        completed_from: Optional[str] = None,
        completed_to: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        include_tags: bool = True,
        media_type: Optional[str] = None,
        search: Optional[str] = None,
        mode: Optional[str] = None,
        preset_id: Optional[str] = None,
        model_name: Optional[str] = None,
        min_rating: Optional[int] = None,
        favorites_only: bool = False,
        collection_id: Optional[str] = None,
        used_phrasebook_value_id: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        system_tag: Optional[str] = None,
        semantic_query: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get generation history with optional filtering.

        Args:
            user_id: The user ID
            limit: Maximum number of results
            offset: Result offset for pagination
            status: Filter by status
            created_from: Filter by creation start date
            created_to: Filter by creation end date
            completed_from: Filter by completion start date
            completed_to: Filter by completion end date
            tag_ids: Filter by tag IDs
            include_tags: Whether to include tags in response
            media_type: Filter by media type (IMAGE, VIDEO)
            semantic_query: Free-text visual description ranked against the
                gallery's image embeddings. When set, results are ordered by
                visual relevance (``sort_by``/``sort_dir`` are ignored) and
                the other filters are applied on top of the ranking - see
                ``_get_semantic_history`` for how the vector query widens so
                a filter-matching item can never be dropped just for ranking
                below the initial top-K.

        Returns:
            Dict with generations, total count, and filter info

        Raises:
            InvalidDateFilterException: If date filters are invalid
        """
        # Validate date parameters
        self.validate_date_filters(created_from, created_to, completed_from, completed_to)

        filter_kwargs = dict(
            user_id=user_id,
            status=status,
            tag_ids=tag_ids,
            created_from=created_from,
            created_to=created_to,
            completed_from=completed_from,
            completed_to=completed_to,
            media_type=media_type,
            search=search,
            mode=mode,
            preset_id=preset_id,
            model_name=model_name,
            min_rating=min_rating,
            favorites_only=favorites_only,
            collection_id=collection_id,
            used_phrasebook_value_id=used_phrasebook_value_id,
            system_tag=system_tag,
        )

        if semantic_query and semantic_query.strip():
            return self._get_semantic_history(
                semantic_query=semantic_query,
                limit=limit,
                offset=offset,
                include_tags=include_tags,
                filter_kwargs=filter_kwargs,
            )

        # Request files and tags to be included when fetching generations
        generations = self.generation_repo.get_all(
            limit=limit,
            offset=offset,
            include_files=True,
            include_tags=include_tags,
            sort_by=sort_by,
            sort_dir=sort_dir,
            **filter_kwargs,
        )

        history_data = self._serialize_generations(generations, include_tags)

        # Get total count with tag filtering
        total_count = self.generation_repo.count_by_status(**filter_kwargs)

        return {
            'generations': history_data,
            'total': total_count,
            'limit': limit,
            'offset': offset,
            'filters': {
                'status': status,
                'tag_ids': tag_ids,
                'created_from': created_from,
                'created_to': created_to,
                'completed_from': completed_from,
                'completed_to': completed_to
            }
        }

    def _serialize_generations(
        self, generations: List[Generation], include_tags: bool
    ) -> List[Dict[str, Any]]:
        """Serialize generations for history payloads and attach system tags."""
        names = self._preset_name_map()
        history_data = []
        for gen in generations:
            gen_dict = gen.to_dict(include_files=True, include_tags=include_tags)
            if gen.preset_id:
                gen_dict['preset_name'] = names.get(gen.preset_id, gen.preset_id)
            else:
                gen_dict['preset_name'] = "Uploaded"
            history_data.append(gen_dict)
        self._attach_system_tags(history_data)
        return history_data

    def _semantic_generation_ids(
        self, user_id: str, semantic_query: str, limit: int
    ) -> List[str]:
        """Visually rank the user's gallery; unique generation ids, best-first."""
        if self.media_indexer is None:
            return []
        try:
            hits = self.media_indexer.search_gallery(user_id, semantic_query, limit=limit)
        except Exception:
            logger.exception("semantic gallery search failed")
            return []
        ordered: List[str] = []
        seen = set()
        for hit in hits:
            generation_id = hit.get("generation_id")
            if generation_id and generation_id not in seen:
                seen.add(generation_id)
                ordered.append(generation_id)
        return ordered

    def _filter_ranked_generations(
        self, ranked_ids: List[str], filter_kwargs: Dict[str, Any]
    ) -> List[Generation]:
        """Intersect ranked ids with the SQL filters, kept in vector order."""
        if not ranked_ids:
            return []
        matched = self.generation_repo.get_all(generation_ids=ranked_ids, **filter_kwargs)
        rank = {gen_id: index for index, gen_id in enumerate(ranked_ids)}
        matched.sort(key=lambda g: rank.get(g.id, len(rank)))
        return matched

    def _get_semantic_history(
        self,
        semantic_query: str,
        limit: Optional[int],
        offset: int,
        include_tags: bool,
        filter_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """History listing ordered by visual relevance to ``semantic_query``.

        The other filters (status, tags, dates, favorites, collections, ...)
        need SQL joins the vector store cannot express - its embedding
        metadata carries nothing but ``generation_id`` - so they are applied
        after ranking, by intersecting ranked ids with a normal
        ``generation_repo.get_all`` call.

        Two different questions need two different queries:

        - "What page do I show, in relevance order?" - a similarity (ANN)
          query, which only ever needs to rank enough of the collection to
          fill the requested page. A single, small vector query would
          silently drop filter-matching items that happen to rank below its
          cutoff, so this widens - doubling from ``SEMANTIC_TOP_K`` - until
          either the page is filled or the widened query has covered the
          whole collection.
        - "How many matches are there in total?" - ranking is irrelevant to
          membership, so this is answered exactly, independent of how far
          the page query widened: every embedded generation id, intersected
          with the filters. Sizing this to whatever the page query happened
          to rank would under-report ``total`` (and break "how many pages
          are there" on the frontend) whenever a filter has more matches
          than one page's worth.
        """
        from src.features.media_index.indexer import SEMANTIC_TOP_K

        user_id = filter_kwargs.get('user_id')
        needed = (offset + limit) if limit else None

        query_limit = SEMANTIC_TOP_K
        ranked_ids = self._semantic_generation_ids(user_id, semantic_query, limit=query_limit)
        ranked_matched = self._filter_ranked_generations(ranked_ids, filter_kwargs)

        # `ranked_ids` is deduped down from file-level hits (several files can
        # share a generation), so its length can't tell "more to find" from
        # "collection exhausted" - `collection_size` (raw vector count) is the
        # only reliable ceiling for the widening loop.
        collection_size: Optional[int] = None
        while ranked_ids and (needed is None or len(ranked_matched) < needed):
            if self.media_indexer is None:
                break
            if collection_size is None:
                collection_size = self.media_indexer.gallery_collection_size(user_id)
            if query_limit >= collection_size:
                break
            query_limit = min(query_limit * 2, collection_size)
            ranked_ids = self._semantic_generation_ids(user_id, semantic_query, limit=query_limit)
            ranked_matched = self._filter_ranked_generations(ranked_ids, filter_kwargs)

        total_count = 0
        if self.media_indexer is not None:
            all_ids = self.media_indexer.all_gallery_generation_ids(user_id)
            if all_ids:
                total_count = len(self._filter_ranked_generations(all_ids, filter_kwargs))

        page = ranked_matched[offset:offset + limit] if limit else ranked_matched[offset:]

        history_data: List[Dict[str, Any]] = []
        if page:
            page_order = {gen.id: index for index, gen in enumerate(page)}
            page_generations = self.generation_repo.get_all(
                user_id=user_id,
                generation_ids=list(page_order),
                include_files=True,
                include_tags=include_tags,
            )
            page_generations.sort(key=lambda g: page_order.get(g.id, len(page_order)))
            history_data = self._serialize_generations(page_generations, include_tags)

        return {
            'generations': history_data,
            'total': total_count,
            'limit': limit,
            'offset': offset,
            'filters': {
                'status': filter_kwargs.get('status'),
                'tag_ids': filter_kwargs.get('tag_ids'),
                'created_from': filter_kwargs.get('created_from'),
                'created_to': filter_kwargs.get('created_to'),
                'completed_from': filter_kwargs.get('completed_from'),
                'completed_to': filter_kwargs.get('completed_to'),
                'semantic_query': semantic_query,
            }
        }

    def _attach_system_tags(self, gen_dicts: List[Dict[str, Any]]) -> None:
        """Decorate each file dict with its system tags and rating scores.

        One batched lookup for the whole page; every file always gets all
        three keys so the frontend can distinguish "not tagged yet" (empty
        list, null scores, nsfw False) without probing for missing fields.
        The ``nsfw`` flag is decided here because the blur threshold is a
        SYSTEM setting regular users cannot read.
        """
        for gen_dict in gen_dicts:
            for file_dict in gen_dict.get('files') or []:
                file_dict['system_tags'] = []
                file_dict['rating_scores'] = None
                file_dict['nsfw'] = False

        if self.media_index_repository is None:
            return

        file_ids = [
            file_dict['id']
            for gen_dict in gen_dicts
            for file_dict in gen_dict.get('files') or []
        ]
        if not file_ids:
            return

        try:
            tag_data = self.media_index_repository.get_for_files(file_ids)
        except Exception:
            logger.debug("system-tag lookup failed", exc_info=True)
            return

        threshold = 0.6
        if self.settings is not None:
            try:
                threshold = float(
                    self.settings.get_setting("media_nsfw_blur_threshold", 0.6)
                )
            except (TypeError, ValueError):
                pass

        for gen_dict in gen_dicts:
            for file_dict in gen_dict.get('files') or []:
                entry = tag_data.get(file_dict['id'])
                if entry:
                    file_dict['system_tags'] = entry['system_tags']
                    ratings = entry['rating_scores'] or None
                    file_dict['rating_scores'] = ratings
                    if ratings:
                        nsfw_score = (
                            ratings.get('questionable', 0.0) + ratings.get('explicit', 0.0)
                        )
                        file_dict['nsfw'] = nsfw_score >= threshold

    def get_facets(self, user_id: str) -> Dict[str, Any]:
        """Return distinct modes, presets and models (with counts) for filter UIs."""
        facets = self.generation_repo.get_facets(user_id=user_id)
        names = self._preset_name_map()
        facets['presets'] = [
            {**preset, 'name': names.get(preset['id'], preset['id'])}
            for preset in facets.get('presets', [])
        ]
        return facets

    def get_by_id(
        self,
        generation_id: str,
        user_id: str,
        include_files: bool = True
    ) -> Dict[str, Any]:
        """Get specific generation by ID with parameters and models.

        Args:
            generation_id: The generation ID
            user_id: The user ID for ownership verification
            include_files: Whether to include file records

        Returns:
            Dict with generation data, parameters, and models

        Raises:
            GenerationNotFoundException: If generation not found
        """
        generation = self._get_generation_or_raise(generation_id, user_id, include_files)

        # Get base generation data
        generation_data = generation.to_dict(include_files=include_files)
        if include_files:
            self._attach_system_tags([generation_data])

        # Fetch and include parameters
        from src.features.generation.parameter_repository import generation_parameter_repo
        parameters = generation_parameter_repo.get_by_generation(generation_id)

        # Format parameters as {param_name: [values]}
        params_dict = {}
        for param in parameters:
            if param.parameter_name not in params_dict:
                params_dict[param.parameter_name] = []
            params_dict[param.parameter_name].append(param.to_dict()['parameter_value'])

        generation_data['parameters'] = params_dict

        # Fetch and include models. Generation history is user-facing, so the models it
        # names carry no path, size or hash - only what the model is. See docs/models.md.
        from src.features.generation.model_repository import generation_model_repo
        models = generation_model_repo.get_by_generation(generation_id)
        generation_data['models'] = [model.to_dict(admin=False) for model in models]

        # Fetch and include prompt segments (with their phrasebook provenance)
        from src.features.generation.segment_repository import generation_segment_repo
        segments = generation_segment_repo.get_by_generation(generation_id)
        generation_data['segments'] = [segment.to_dict() for segment in segments]

        # Fetch and include tags (get_history's list path already does this;
        # this by-id path never did, so every detail fetch reported no tags).
        from src.features.tags.repository import tag_repo
        tags = tag_repo.get_generation_tags(generation_id)
        generation_data['tags'] = [tag.model_dump(mode="json") for tag in tags]

        # Whether a resource profile was captured for this run (admin-only viewer
        # keys off this). Cheap file-existence check; the profile content itself
        # stays behind the admin-gated profile endpoint.
        generation_data['has_profile'] = self._has_profile(generation_id)

        return generation_data

    def _has_profile(self, generation_id: str) -> bool:
        """Whether a ``profile.jsonl`` exists on disk for this generation.

        Best-effort: ``False`` when no file store was provided or the storage
        path can't be resolved -- this flag must never break the generation
        detail response it rides on."""
        if self.file_service is None:
            return False
        try:
            return profile_paths.has_profile(
                self.file_service.base_storage_dir, generation_id
            )
        except Exception:
            logger.debug("has_profile check failed for %s", generation_id, exc_info=True)
            return False

    def get_params(
        self,
        generation_id: str,
        index: int,
        user_id: str
    ) -> Dict[str, Any]:
        """Get parameters for a specific generation and image index.

        When `generation_id` carries a `generation_sources` link (a
        standalone "enhance" run whose source field was seeded from a prior
        generation's output, rather than a bare upload - see
        `src/features/generation/orchestrator.py`'s `_parse_generation_origins`),
        params/models are inherited from the linked source generation: an own
        value that is present and non-empty wins; a missing key or an
        empty-string value falls back to the source's value at the same key.
        Models are a union (own first, then any source model not already
        present by `id`). The source's own params are themselves resolved
        through this same inheritance, so an enhance-of-enhance chain
        resolves all the way back to its root, up to
        `_MAX_PROVENANCE_DEPTH` hops (cycle-guarded).

        A generation with more than one origin field (multiple media inputs)
        only inherits from the first one, by `field_name` sort order - see
        `GenerationSourceRepository.get_by_generation`.

        Ownership is checked once, here, for `generation_id` itself; inherited
        hops are not re-checked against `user_id` because submission-time
        validation (`_validate_generation_origins`) already guaranteed every
        link points at a generation owned by the same user who created it.

        Args:
            generation_id: The generation ID
            index: The image index
            user_id: The user ID for ownership verification

        Returns:
            Dict with generation_id, index, parameters, and models

        Raises:
            GenerationNotFoundException: If generation not found
        """
        # Verify the user owns this generation
        self._get_generation_or_raise(generation_id, user_id)

        param_dict, models = self._resolve_params_and_models(generation_id, index, visited=set())

        return {
            "generation_id": generation_id,
            "index": index,
            "parameters": param_dict,
            "models": models,
        }

    def _resolve_params_and_models(
        self,
        generation_id: str,
        index: int,
        visited: set,
        depth: int = 0,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Own params/models for (generation_id, index), merged with whatever
        `generation_id`'s provenance link (if any) inherits from its source.
        See `get_params`'s docstring for the exact merge rule. No ownership
        check here - only `get_params` gates on `user_id`."""
        from src.features.generation.parameter_repository import generation_parameter_repo
        from src.features.generation.model_repository import generation_model_repo
        from src.features.generation.source_repository import generation_source_repo

        own_param_rows = generation_parameter_repo.get_by_generation_and_index(generation_id, index)
        own_params = {p.parameter_name: p.to_dict()['parameter_value'] for p in own_param_rows}

        own_models = [
            model.to_dict(include_providers=True, include_tags=False)
            for model in generation_model_repo.get_by_generation(
                generation_id, include_model_info=True, include_files=True
            )
        ]

        if generation_id in visited or depth >= self._MAX_PROVENANCE_DEPTH:
            return own_params, own_models

        source_link = generation_source_repo.get_primary_for_generation(generation_id)
        if source_link is None:
            return own_params, own_models

        source_generation = self.generation_repo.get_by_id(source_link.source_generation_id)
        if source_generation is None:
            return own_params, own_models

        inherited_params, inherited_models = self._resolve_params_and_models(
            source_link.source_generation_id,
            source_link.source_file_index,
            visited | {generation_id},
            depth + 1,
        )

        merged_params = dict(own_params)
        for key, value in inherited_params.items():
            if key not in merged_params or merged_params[key] in (None, ""):
                merged_params[key] = value

        merged_models = list(own_models)
        seen_ids = {m.get('id') for m in own_models}
        for model in inherited_models:
            model_id = model.get('id')
            if model_id is not None and model_id in seen_ids:
                continue
            merged_models.append(model)
            if model_id is not None:
                seen_ids.add(model_id)

        return merged_params, merged_models

    def count_generations_by_tags(self, tag_ids: List[str], user_id: str) -> int:
        """Count generations that have ALL specified tags (for confirmation UI).

        Args:
            tag_ids: List of tag IDs (AND logic)
            user_id: The user ID for ownership verification

        Returns:
            Number of matching generations
        """
        if not tag_ids:
            return 0
        self._validate_tag_ids(tag_ids, user_id)
        from src.features.tags.repository import tag_repo
        generation_ids = tag_repo.get_generations_by_tags(tag_ids, user_id)
        return len(generation_ids)

    def get_tags(self, generation_id: str, user_id: str) -> List[Dict[str, Any]]:
        """Get all tags for a generation.

        Args:
            generation_id: The generation ID
            user_id: The user ID for ownership verification

        Returns:
            List of tag dicts

        Raises:
            GenerationNotFoundException: If generation not found
        """
        # Verify ownership
        self._get_generation_or_raise(generation_id, user_id)

        from src.features.tags.repository import tag_repo
        tags = tag_repo.get_generation_tags(generation_id)
        return [tag.model_dump(mode="json") for tag in tags]
