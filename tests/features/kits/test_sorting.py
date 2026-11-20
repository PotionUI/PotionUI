from tests.features.kits.test_routes import RoutesBase


class TestKitSorting(RoutesBase):

    def _seed(self):
        ids = {}
        for name, created, updated in [
            ("banana", "2026-01-02 00:00:00", "2026-03-01 00:00:00"),
            ("Apple", "2026-01-03 00:00:00", "2026-02-01 00:00:00"),
            ("cherry", "2026-01-01 00:00:00", "2026-04-01 00:00:00"),
        ]:
            kit = self.kit(name)
            ids[name] = kit["id"]
            with self.db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE kits SET created_at = ?, updated_at = ? WHERE id = ?", (created, updated, kit["id"])
                )
        return ids

    def _names(self, **params):
        response = self.call("GET", "", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return [k["name"] for k in response.json()["data"]["kits"]]

    def test_default_is_the_manual_order(self):
        self._seed()
        self.assertEqual(self._names(), ["banana", "Apple", "cherry"])
        self.assertEqual(self._names(sort_by="manual"), ["banana", "Apple", "cherry"])
        self.assertEqual(self._names(sort_by="manual", sort_dir="desc"), ["banana", "Apple", "cherry"])

    def test_name_is_case_insensitive(self):
        self._seed()
        self.assertEqual(self._names(sort_by="name"), ["Apple", "banana", "cherry"])
        self.assertEqual(self._names(sort_by="name", sort_dir="desc"), ["cherry", "banana", "Apple"])

    def test_created_both_directions(self):
        self._seed()
        self.assertEqual(self._names(sort_by="created"), ["Apple", "banana", "cherry"])
        self.assertEqual(self._names(sort_by="created", sort_dir="asc"), ["cherry", "banana", "Apple"])

    def test_updated_both_directions(self):
        self._seed()
        self.assertEqual(self._names(sort_by="updated"), ["cherry", "banana", "Apple"])
        self.assertEqual(self._names(sort_by="updated", sort_dir="asc"), ["Apple", "banana", "cherry"])

    def test_unknown_field_or_direction_is_422(self):
        for params in ({"sort_by": "color"}, {"sort_dir": "sideways"}):
            self.assertEqual(self.call("GET", "", params=params).status_code, 422)
        body = self.call("GET", "", params={"sort_by": "color"}).text
        for allowed in ("manual", "name", "created", "updated"):
            self.assertIn(allowed, body)

    def test_ties_paginate_without_duplicates_or_gaps(self):
        for index in range(7):
            self.kit(f"same{index}")
        with self.db.get_cursor() as cursor:
            cursor.execute("UPDATE kits SET name = 'same', created_at = '2026-01-01 00:00:00', updated_at = '2026-01-01 00:00:00'")
        for sort_by in ("name", "created", "updated"):
            for direction in ("asc", "desc"):
                seen = []
                for offset in (0, 3, 6):
                    response = self.call("GET", "", params={"sort_by": sort_by, "sort_dir": direction, "limit": 3, "offset": offset})
                    seen += [k["id"] for k in response.json()["data"]["kits"]]
                self.assertEqual(len(seen), 7)
                self.assertEqual(len(set(seen)), 7)

    def test_sort_composes_with_search(self):
        self._seed()
        self.assertEqual(self._names(search="an", sort_by="name"), ["banana"])
