from tests.features.library.test_routes import LibraryRoutesTestBase


class TestLibrarySorting(LibraryRoutesTestBase):

    def _seed(self):
        rows = [
            ("a.png", "Banana.png", 300, "2026-01-02 00:00:00"),
            ("b.png", "apple.png", 100, "2026-01-03 00:00:00"),
            ("c.png", "Cherry.png", 200, "2026-01-01 00:00:00"),
        ]
        for filename, original, size, created in rows:
            item = self._upload(filename=filename, original_filename=original)
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE uploads SET file_size = ?, created_at = ? WHERE id = ?", (size, created, item.id)
                )

    def _names(self, **params):
        response = self.client.get("/api/library/items", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return [i["original_filename"] for i in response.json()["data"]["items"]]

    def test_default_is_newest_first(self):
        self._seed()
        self.assertEqual(self._names(), ["apple.png", "Banana.png", "Cherry.png"])
        self.assertEqual(self._names(sort_by="added"), ["apple.png", "Banana.png", "Cherry.png"])

    def test_added_both_directions(self):
        self._seed()
        self.assertEqual(self._names(sort_by="added", sort_dir="asc"), ["Cherry.png", "Banana.png", "apple.png"])
        self.assertEqual(self._names(sort_by="added", sort_dir="desc"), ["apple.png", "Banana.png", "Cherry.png"])

    def test_name_is_case_insensitive_and_ascending_by_default(self):
        self._seed()
        self.assertEqual(self._names(sort_by="name"), ["apple.png", "Banana.png", "Cherry.png"])
        self.assertEqual(self._names(sort_by="name", sort_dir="desc"), ["Cherry.png", "Banana.png", "apple.png"])

    def test_size_is_largest_first_by_default(self):
        self._seed()
        self.assertEqual(self._names(sort_by="size"), ["Banana.png", "Cherry.png", "apple.png"])
        self.assertEqual(self._names(sort_by="size", sort_dir="asc"), ["apple.png", "Cherry.png", "Banana.png"])

    def test_unknown_field_or_direction_is_422(self):
        for params in ({"sort_by": "color"}, {"sort_dir": "sideways"}):
            response = self.client.get("/api/library/items", params=params)
            self.assertEqual(response.status_code, 422)
        body = self.client.get("/api/library/items", params={"sort_by": "color"}).text
        for allowed in ("added", "name", "size"):
            self.assertIn(allowed, body)

    def test_ties_paginate_without_duplicates_or_gaps(self):
        for index in range(7):
            item = self._upload(filename=f"t{index}.png", original_filename="same.png")
            with self.db.get_cursor() as cursor:
                cursor.execute("UPDATE uploads SET file_size = 5 WHERE id = ?", (item.id,))
        for sort_by in ("name", "size", "added"):
            for direction in ("asc", "desc"):
                seen = []
                for offset in (0, 3, 6):
                    response = self.client.get("/api/library/items", params={
                        "sort_by": sort_by, "sort_dir": direction, "limit": 3, "offset": offset,
                    })
                    seen += [i["id"] for i in response.json()["data"]["items"]]
                self.assertEqual(len(seen), 7)
                self.assertEqual(len(set(seen)), 7)

    def test_sort_composes_with_filters(self):
        self._seed()
        self._upload(filename="v.mp4", media_type="video", original_filename="aaa.mp4")
        self.assertEqual(self._names(media_type="image", sort_by="name"), ["apple.png", "Banana.png", "Cherry.png"])
        response = self.client.get("/api/library/items", params={"media_type": "image", "sort_by": "name", "limit": 2})
        self.assertEqual(response.json()["data"]["total"], 3)
