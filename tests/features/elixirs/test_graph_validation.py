import pytest

from src.features.elixirs.graph import validate_graph
from src.features.elixirs.limits import Limits

KREA = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
H3 = "01KX47H3MINIMAX000000000VA"


def library(item_id, media_type="image"):
    return {"kind": "library", "item_id": item_id, "media_type": media_type, "original_filename": f"{item_id}.png"}


def media(node_id, assets, media_type="image", title=None):
    return {"id": node_id, "kind": "media", "title": title, "media_type": media_type, "assets": assets}


def preset(node_id, preset_id, mode, title=None, form_data=None, overrides=None, snapshot=True):
    return {
        "id": node_id,
        "kind": "preset",
        "title": title,
        "preset_id": preset_id,
        "mode": mode,
        "overrides": overrides or {},
        "session_snapshot": {"prompt": "p", "formData": form_data or {}} if snapshot else None,
    }


def edge(edge_id, source, source_handle, target, target_handle):
    return {
        "id": edge_id,
        "source": source,
        "source_handle": source_handle,
        "target": target,
        "target_handle": target_handle,
        "iteration": "each",
    }


@pytest.fixture
def check(catalog, contracts):
    def run(nodes, edges, run_mode="automatic", limits=None, unavailable=None):
        return validate_graph(
            {"nodes": nodes, "edges": edges},
            run_mode=run_mode,
            catalog=catalog,
            contracts=contracts,
            limits=limits or Limits(),
            unavailable=unavailable,
        )

    return run


def codes(report, severity=None):
    return [issue["code"] for issue in report["issues"] if severity is None or issue["severity"] == severity]


def product_example():
    nodes = [
        media("product", [library("photo-1")], title="Product images"),
        preset("orbit", H3, "video", title="Orbit left"),
        preset("closeup", H3, "video", title="Close-up reveal"),
    ]
    edges = [
        edge("to-orbit", "product", "images", "orbit", "first_frame"),
        edge("to-closeup", "product", "images", "closeup", "first_frame"),
    ]
    return nodes, edges


def test_one_library_photo_feeding_two_video_presets_is_valid_and_starts_two_jobs(check):
    nodes, edges = product_example()

    report = check(nodes, edges)

    assert report["valid"], report["issues"]
    assert report["issues"] == []
    assert report["estimates"]["total_invocations"] == 2
    assert report["estimates"]["edges"] == {"to-orbit": 1, "to-closeup": 1}
    assert report["estimates"]["nodes"]["product"] == {"invocations": 0, "outputs": 1}
    assert report["order"][0] == "product"


def test_a_generated_photo_shared_by_two_branches_runs_once_and_counts_sum(check):
    nodes = [
        preset("photo", KREA, "txt2img", title="Product photo", form_data={"quantity": 3}),
        preset("orbit", H3, "video", title="Orbit left"),
        preset("closeup", H3, "video", title="Close-up reveal", form_data={"quantity": 2}),
    ]
    edges = [
        edge("a", "photo", "images", "orbit", "first_frame"),
        edge("b", "photo", "images", "closeup", "first_frame"),
    ]

    estimates = check(nodes, edges)["estimates"]

    assert estimates["nodes"]["photo"] == {"invocations": 1, "outputs": 3}
    assert estimates["nodes"]["orbit"] == {"invocations": 3, "outputs": 3}
    assert estimates["nodes"]["closeup"] == {"invocations": 3, "outputs": 6}
    assert estimates["total_invocations"] == 7


def test_three_photos_into_two_branches_make_six_jobs_without_generating_the_source(check):
    nodes, edges = product_example()
    nodes[0]["assets"] = [library("p1"), library("p2"), library("p3")]

    estimates = check(nodes, edges)["estimates"]

    assert estimates["total_invocations"] == 6
    assert estimates["nodes"]["product"]["invocations"] == 0


def test_reordering_nodes_and_connections_changes_nothing(check):
    nodes, edges = product_example()
    forward = check(nodes, edges)
    backward = check(list(reversed(nodes)), list(reversed(edges)))

    assert backward["valid"] == forward["valid"]
    assert backward["estimates"] == forward["estimates"]
    assert backward["order"].index("product") < backward["order"].index("orbit")


def test_a_loop_is_rejected_and_names_every_node_in_it(check):
    nodes = [
        preset("a", KREA, "enhance", title="Sharpen"),
        preset("b", KREA, "enhance", title="Polish"),
        preset("c", KREA, "enhance", title="Finish"),
    ]
    edges = [
        edge("ab", "a", "images", "b", "source_image"),
        edge("bc", "b", "images", "c", "source_image"),
        edge("ca", "c", "images", "a", "source_image"),
    ]

    report = check(nodes, edges)

    assert not report["valid"]
    loop = [issue for issue in report["issues"] if issue["code"] == "cycle"]
    assert len(loop) == 1
    assert "Sharpen" in loop[0]["message"] and "Polish" in loop[0]["message"] and "Finish" in loop[0]["message"]
    assert report["estimates"] is None


def test_a_branch_hanging_off_a_loop_is_not_reported_as_part_of_it(check):
    nodes = [
        preset("a", KREA, "enhance", title="Sharpen"),
        preset("b", KREA, "enhance", title="Polish"),
        preset("tail", KREA, "enhance", title="Tail"),
    ]
    edges = [
        edge("ab", "a", "images", "b", "source_image"),
        edge("ba", "b", "images", "a", "source_image"),
        edge("bt", "b", "images", "tail", "source_image"),
    ]

    loop = [issue for issue in check(nodes, edges)["issues"] if issue["code"] == "cycle"]

    assert len(loop) == 1
    assert "Tail" not in loop[0]["message"]


def test_a_node_cannot_feed_itself(check):
    nodes = [preset("a", KREA, "enhance", title="Sharpen")]

    report = check(nodes, [edge("aa", "a", "images", "a", "source_image")])

    assert "self_connection" in codes(report)
    assert "Sharpen" in report["issues"][0]["message"]


def test_a_media_source_cannot_receive_a_connection(check):
    nodes = [media("one", [library("x")], title="First"), media("two", [library("y")], title="Second")]

    report = check(nodes, [edge("e", "one", "images", "two", "images")])

    assert codes(report) == ["media_source_has_input"]
    assert "Second" in report["issues"][0]["message"]


def test_a_preset_takes_only_one_varying_input(check):
    nodes = [
        media("photos", [library("x")], title="Photos"),
        preset("photo", KREA, "txt2img", title="Product photo"),
        preset("orbit", H3, "video", title="Orbit left"),
    ]
    edges = [
        edge("a", "photos", "images", "orbit", "first_frame"),
        edge("b", "photo", "images", "orbit", "last_frame"),
    ]

    report = check(nodes, edges)

    assert codes(report) == ["too_many_inputs"]
    message = report["issues"][0]["message"]
    assert message.startswith("Orbit left") and "Photos" in message and "Product photo" in message


def test_media_of_the_wrong_type_cannot_connect(check):
    nodes = [media("clips", [library("v", "video")], media_type="video", title="Clips"), preset("orbit", H3, "video", title="Orbit left")]

    report = check(nodes, [edge("e", "clips", "videos", "orbit", "first_frame")])

    assert codes(report) == ["incompatible_media"]
    assert report["issues"][0]["edge_id"] == "e"


def test_a_video_source_can_feed_a_video_upscaler(check):
    nodes = [media("clips", [library("v", "video")], media_type="video"), preset("up", H3, "upscale")]

    report = check(nodes, [edge("e", "clips", "videos", "up", "input_video")])

    assert report["valid"], report["issues"]


def test_unknown_ports_are_named(check):
    nodes = [media("photos", [library("x")], title="Photos"), preset("orbit", H3, "video", title="Orbit left")]

    report = check(nodes, [edge("e", "photos", "videos", "orbit", "middle_frame")])

    assert codes(report) == ["unknown_output", "unknown_input"]


def test_connections_to_missing_nodes_are_reported(check):
    report = check([preset("orbit", H3, "video")], [edge("e", "gone", "images", "orbit", "first_frame")])

    assert codes(report) == ["unknown_edge_endpoint"]
    assert "gone" in report["issues"][0]["message"]


def test_duplicate_ids_and_connections_are_reported(check):
    nodes, edges = product_example()
    nodes.append(preset("orbit", H3, "video"))
    edges.append(dict(edges[0]))
    edges.append({**edges[0], "id": "again"})

    found = codes(check(nodes, edges))

    assert "duplicate_node_id" in found
    assert "duplicate_edge_id" in found
    assert "duplicate_connection" in found


def test_preset_problems_are_explained_per_node(check, catalog):
    nodes = [
        {"id": "blank", "kind": "preset", "title": "Blank", "overrides": {}},
        preset("gone", "NOT-INSTALLED", "txt2img", title="Gone"),
        preset("oldmode", KREA, "img2img", title="Old mode"),
        preset("nocontract", H3, "refs", title="References"),
        preset("nosession", KREA, "txt2img", title="No session", snapshot=False),
    ]

    report = check(nodes, [])

    by_node = {issue["node_id"]: issue for issue in report["issues"]}
    assert by_node["blank"]["code"] == "no_preset"
    assert by_node["gone"]["code"] == "unknown_preset"
    assert by_node["oldmode"]["code"] == "unknown_mode"
    assert by_node["nocontract"]["code"] == "no_contract"
    assert by_node["nosession"]["code"] == "no_session"
    for node in nodes:
        assert by_node[node["id"]]["message"].startswith(node["title"])


def test_untitled_nodes_are_named_by_kind_or_preset_and_mode(check):
    report = check(
        [
            preset("n7", KREA, "txt2img", snapshot=False),
            {"id": "blank", "kind": "preset", "overrides": {}},
            media("m", [], title="   "),
        ],
        [],
    )

    messages = [issue["message"] for issue in report["issues"]]
    assert messages[0].startswith("Krea-2 · Text to image has no saved session")
    assert messages[1].startswith("Untitled preset node has no preset")
    assert messages[2].startswith("Untitled media source has no files")


def test_a_changed_preset_marks_its_contract_out_of_date(check, catalog):
    del catalog[KREA]["modes"]["enhance"]["forms"]["custom"]["source_image"]

    report = check([preset("e", KREA, "enhance", title="Sharpen")], [])

    assert "contract_out_of_date" in codes(report)


def test_a_missing_form_variant_is_reported(check):
    node = preset("p", KREA, "txt2img", title="Photo")
    node["form_name"] = "retired"

    assert codes(check([node], [])) == ["unknown_form"]


def test_a_required_input_must_be_connected_or_saved(check):
    unbound = check([preset("e", KREA, "enhance", title="Sharpen")], [])
    saved = check([preset("e", KREA, "enhance", form_data={"source_image": "uploads/a.png"})], [])
    overridden = check([preset("e", KREA, "enhance", overrides={"formData": {"source_image": "uploads/b.png"}})], [])

    assert codes(unbound) == ["missing_input"]
    assert "source image" in unbound["issues"][0]["message"]
    assert saved["valid"] and overridden["valid"]


def test_a_connection_replacing_a_saved_input_warns_but_stays_valid(check):
    nodes = [
        media("photos", [library("x")]),
        preset("e", KREA, "enhance", title="Sharpen", form_data={"source_image": "uploads/a.png"}),
    ]

    report = check(nodes, [edge("c", "photos", "images", "e", "source_image")])

    assert report["valid"]
    assert codes(report, "warning") == ["input_replaces_saved"]


def test_media_source_problems_are_reported(check):
    nodes = [
        media("empty", [], title="Empty"),
        media("twice", [library("x"), library("x")], title="Twice"),
        media("mixed", [library("x"), library("v", "video")], title="Mixed"),
        media("untyped", [library("x")], media_type=None, title="Untyped"),
    ]

    by_node = {issue["node_id"]: issue["code"] for issue in check(nodes, [])["issues"]}

    assert by_node == {
        "empty": "no_media",
        "twice": "duplicate_media",
        "mixed": "mixed_media",
        "untyped": "media_type_missing",
    }


def test_each_unavailable_file_is_reported_on_its_own(check):
    nodes = [media("photos", [library("a"), library("b"), library("c")], title="Photos")]

    report = check(nodes, [], unavailable={"photos": [(0, "it was deleted"), (2, "it was deleted")]})

    messages = [issue["message"] for issue in report["issues"] if issue["code"] == "unavailable_media"]
    assert len(messages) == 2
    assert "file 1" in messages[0] and "file 3" in messages[1]


def test_size_limits_are_enforced(check):
    nodes, edges = product_example()

    found = codes(check(nodes, edges, limits=Limits(max_nodes=2, max_edges=1, max_branches=1)))

    assert {"too_many_nodes", "too_many_edges", "too_many_branches"} <= set(found)


def test_a_media_source_cannot_hold_more_files_than_allowed(check):
    nodes = [media("photos", [library("a"), library("b")], title="Photos")]

    assert codes(check(nodes, [], limits=Limits(max_media_per_source=1))) == ["too_many_media"]


def test_too_many_jobs_block_an_automatic_brew(check):
    nodes, edges = product_example()
    nodes[0]["assets"] = [library(f"p{i}") for i in range(3)]

    report = check(nodes, edges, limits=Limits(max_invocations=5, max_outputs_per_connection=2))

    assert not report["valid"]
    assert {"too_many_invocations", "too_many_outputs_on_edge"} <= set(codes(report, "error"))


def test_too_many_jobs_only_warn_when_outputs_are_chosen_between_presets(check):
    nodes, edges = product_example()
    nodes[0]["assets"] = [library(f"p{i}") for i in range(3)]

    report = check(nodes, edges, run_mode="review", limits=Limits(max_invocations=5, max_outputs_per_connection=2))

    assert report["valid"]
    assert {"too_many_invocations", "too_many_outputs_on_edge"} <= set(codes(report, "warning"))


def fan_out(count):
    nodes = [media("photos", [library("x")], title="Photos")]
    edges = []
    for index in range(count):
        nodes.append(preset(f"v{index}", H3, "video", title=f"Video {index}"))
        edges.append(edge(f"e{index}", "photos", "images", f"v{index}", "first_frame"))
    return nodes, edges


def standalone_sources(count):
    return [media(f"m{index}", [library(f"x{index}")]) for index in range(count)], []


def one_source_of(count):
    nodes = [media("photos", [library(f"p{index}") for index in range(count)], title="Photos")]
    return nodes, []


def source_into_video(count):
    nodes = [media("photos", [library(f"p{index}") for index in range(count)], title="Photos"), preset("v", H3, "video")]
    return nodes, [edge("e", "photos", "images", "v", "first_frame")]


LIMIT_CASES = [
    ("max_nodes", standalone_sources, "too_many_nodes"),
    ("max_edges", fan_out, "too_many_edges"),
    ("max_branches", fan_out, "too_many_branches"),
    ("max_media_per_source", one_source_of, "too_many_media"),
    ("max_outputs_per_connection", source_into_video, "too_many_outputs_on_edge"),
    ("max_invocations", source_into_video, "too_many_invocations"),
]


@pytest.mark.parametrize("limit, build, code", LIMIT_CASES, ids=[case[0] for case in LIMIT_CASES])
def test_a_graph_exactly_at_a_limit_is_accepted(check, limit, build, code):
    nodes, edges = build(3)

    report = check(nodes, edges, limits=Limits(**{limit: 3}))

    assert code not in codes(report)
    assert report["valid"], report["issues"]


@pytest.mark.parametrize("limit, build, code", LIMIT_CASES, ids=[case[0] for case in LIMIT_CASES])
def test_a_graph_one_past_a_limit_is_refused(check, limit, build, code):
    nodes, edges = build(4)

    report = check(nodes, edges, limits=Limits(**{limit: 3}))

    assert codes(report, "error").count(code) == 1
    assert not report["valid"]


def test_a_frame_input_needs_the_preset_mode_to_support_it(check, catalog):
    catalog[H3]["director_shapes"]["video"] = ["t2v", "i2v"]
    nodes = [media("photos", [library("p1")]), preset("orbit", H3, "video", title="Orbit left")]

    first = check(nodes, [edge("e1", "photos", "images", "orbit", "first_frame")])
    last = check(nodes, [edge("e1", "photos", "images", "orbit", "last_frame")])

    assert "director_role_unsupported" not in codes(first)
    problem = next(issue for issue in last["issues"] if issue["code"] == "director_role_unsupported")
    assert problem["node_id"] == "orbit"
    assert problem["message"] == "Orbit left cannot end on an image in this mode. Remove the last frame connection."
    assert not last["valid"]


def test_a_step_added_before_the_start_feeds_it_and_becomes_the_first_in_order(check):
    before_nodes = [preset("orbit", H3, "video", title="Orbit left")]

    nodes = [preset("photo", KREA, "txt2img", title="Product photo")] + before_nodes
    edges = [edge("new-start", "photo", "images", "orbit", "first_frame")]
    report = check(nodes, edges)

    assert report["valid"], report["issues"]
    assert report["order"] == ["photo", "orbit"]
    assert report["estimates"]["nodes"]["orbit"]["invocations"] == 1


def test_a_files_source_added_before_the_start_feeds_it(check):
    nodes = [media("files", [library("photo-1")], title="Product images"), preset("orbit", H3, "video", title="Orbit left")]
    edges = [edge("new-start", "files", "images", "orbit", "first_frame")]

    report = check(nodes, edges)

    assert report["valid"], report["issues"]
    assert report["order"][0] == "files"
