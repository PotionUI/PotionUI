from backend.mapping import model_spec

GEMINI = {
    "id": "google/gemini-2.5-flash-image",
    "name": "Nano Banana",
    "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["image", "text"]},
    "supported_parameters": ["max_tokens", "temperature", "seed"],
}


def params_of(item):
    return {param.name: param for param in model_spec(item, []).params}


def test_gemini_image_keeps_an_aspect_ratio_control_when_the_catalog_omits_it():
    params = params_of(GEMINI)

    assert params["aspect_ratio"].kind == "enum"
    assert {"1:1", "16:9", "9:16"} <= set(params["aspect_ratio"].values)


def test_a_bare_aspect_ratio_name_still_offers_choices():
    item = {**GEMINI, "id": "vendor/other", "supported_parameters": ["aspect_ratio"]}

    assert params_of(item)["aspect_ratio"].values


def test_listed_aspect_ratios_win_over_the_fallback():
    item = {**GEMINI, "supported_parameters": [{"name": "aspect_ratio", "type": "enum", "values": ["1:1", "3:2"]}]}

    assert params_of(item)["aspect_ratio"].values == ("1:1", "3:2")


def test_an_unrelated_model_without_the_parameter_gets_none():
    item = {**GEMINI, "id": "vendor/plain"}

    assert "aspect_ratio" not in params_of(item)
