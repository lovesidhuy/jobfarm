from core.browser.profile_lease import ProfileLease


class _FakeTable:
    def __init__(self):
        self.delete_kwargs = None

    def delete_item(self, **kwargs):
        self.delete_kwargs = kwargs


def test_release_aliases_reserved_owner_attribute():
    lease = ProfileLease("profile")
    table = _FakeTable()
    lease._table = table

    lease.release()

    assert table.delete_kwargs["ConditionExpression"] == "#owner = :owner"
    assert table.delete_kwargs["ExpressionAttributeNames"] == {"#owner": "owner"}
    assert table.delete_kwargs["ExpressionAttributeValues"] == {":owner": lease.owner}
    assert lease._table is None
