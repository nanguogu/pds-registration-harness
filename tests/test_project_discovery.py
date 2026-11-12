from pds_harness.services.project_discovery import (
    discover_pending_registration_projects,
)


def test_filters_exact_pending_registration_status():
    payload = {
        "code": 10000,
        "data": {
            "data": {
                "list": [
                    {
                        "programId": 1,
                        "code": "STS1",
                        "softwareName": "A",
                        "status": "待计划编制",
                        "statusValue": "待计划编制",
                        "testTypeValue": "软件产品登记测试",
                        "wasKeepSecret": "不保密",
                    },
                    {
                        "programId": 2,
                        "code": "STS2",
                        "softwareName": "B",
                        "status": "待提交",
                        "testTypeValue": "软件产品登记测试",
                    },
                    {
                        "programId": 3,
                        "code": "STS3",
                        "softwareName": "C",
                        "status": "待计划编制",
                        "testTypeValue": "验收测试",
                    },
                ]
            }
        },
    }
    projects = discover_pending_registration_projects(payload)
    assert [project.program_code for project in projects] == ["STS1"]

