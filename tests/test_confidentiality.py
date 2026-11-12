import pytest

from pds_harness.domain.errors import CloudPolicyError
from pds_harness.services.confidentiality import (
    decide_cloud_access,
    require_cloud_access,
)


@pytest.mark.parametrize("value", ["不保密", "  不 保 密  ", "不\u3000保密"])
def test_exact_non_confidential_value_allows_cloud(value):
    assert decide_cloud_access(value).allowed is True


@pytest.mark.parametrize("value", [None, "", "保密", "内部", "不保密项目"])
def test_unknown_or_other_value_denies_cloud(value):
    assert decide_cloud_access(value).allowed is False
    with pytest.raises(CloudPolicyError):
        require_cloud_access(value)

