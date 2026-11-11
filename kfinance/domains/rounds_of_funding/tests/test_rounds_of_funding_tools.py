from copy import deepcopy
from decimal import Decimal

import httpx
from pydantic import ValidationError
import pytest
from pytest_httpx import HTTPXMock

from kfinance.async_batch_execution import (
    MAX_BATCH_TASKS,
    AsyncTask,
    batch_execute_async_tasks,
)
from kfinance.conftest import FAKE_COMPANY_1_ID_TRIPLE, FAKE_COMPANY_2_ID_TRIPLE, SPGI_ID_TRIPLE
from kfinance.domains.companies.company_models import COMPANY_ID_PREFIX
from kfinance.domains.rounds_of_funding import rounds_of_funding_tools
from kfinance.domains.rounds_of_funding.rounds_of_funding_models import (
    AdvisorResp,
    CompanyIdAndNameWithAdvisors,
    FundingSummary,
    InvestorInRoundOfFundingWithAdvisors,
    RoundOfFundingInfoSecurity,
    RoundOfFundingInfoTimeline,
    RoundOfFundingInfoTransaction,
    RoundOfFundingInfoWithAdvisors,
    RoundOfFundingParticipantsWithAdvisors,
    RoundsOfFundingResp,
    RoundsOfFundingRole,
)
from kfinance.domains.rounds_of_funding.rounds_of_funding_tools import (
    GetFundingSummaryFromIdentifiersResp,
    GetRoundsOfFundingFromIdentifiersResp,
    GetRoundsOfFundingInfoFromTransactionIdsArgs,
    GetRoundsOfFundingInfoFromTransactionIdsResp,
    fetch_rounds_of_funding_from_company_id,
    get_funding_summary_from_identifiers,
    get_rounds_of_funding_from_identifiers,
    get_rounds_of_funding_info_from_transaction_ids,
)
from kfinance.integrations.tool_calling.tool_calling_models import MAX_TOOL_LIST_ARG_LENGTH


class TestRoundsOfFunding:
    funding_round_response = {
        "timeline": {
            "announced_date": "2023-01-15",
            "closed_date": "2023-02-15",
        },
        "participants": {
            "target": {
                "company_id": 12345,
                "company_name": "Target Company Inc.",
            },
            "investors": [
                {
                    "company_id": 67890,
                    "company_name": "Investor LLC",
                    "lead_investor": True,
                    "investment_value": "2500000.00000000",
                    "currency": "USD",
                    "ownership_percentage_pre": "0.0000",
                    "ownership_percentage_post": "15.5000",
                    "board_seat_granted": True,
                },
                {
                    "company_id": 98765,
                    "company_name": "Secondary Investor Corp",
                    "lead_investor": False,
                    "investment_value": "1000000.00000000",
                    "currency": "USD",
                    "ownership_percentage_pre": "0.0000",
                    "ownership_percentage_post": "6.2000",
                    "board_seat_granted": False,
                },
            ],
        },
        "transaction": {
            "funding_type": "Series A",
            "amount_offered": "5000000.00000000",
            "currency": "USD",
            "pre_money_valuation": "25000000.00000000",
            "post_money_valuation": "30000000.00000000",
            "use_of_proceeds": "Product development and market expansion",
            "aggregate_amount_raised": "5000000.00000000",
        },
        "security": {
            "security_description": "Series A Preferred Stock",
            "seniority_level": "Senior",
        },
    }

    rounds_of_funding_response = {
        "rounds_of_funding": [
            {
                "transaction_id": 123456,
                "funding_round_notes": "Series A funding round",
                "closed_date": "2023-02-15",
                "funding_type": "Series A",
            }
        ]
    }

    # Different transaction ID for funding summary test to avoid mock conflicts
    funding_summary_rounds_response = {
        "rounds_of_funding": [
            {
                "transaction_id": 789012,
                "funding_round_notes": "Series A funding round for summary test",
                "closed_date": "2023-02-15",
                "funding_type": "Series A",
            }
        ]
    }

    target_advisors_response = {
        "advisors": [
            {
                "advisor_company_id": 11111,
                "advisor_company_name": "Legal Advisors LLP",
                "advisor_type_name": "Legal Counsel",
                "advisor_fee_amount": 75000.0,
                "advisor_fee_currency": "USD",
                "is_lead": True,
            },
            {
                "advisor_company_id": 22222,
                "advisor_company_name": "Investment Bank Inc",
                "advisor_type_name": "Financial Advisor",
                "advisor_fee_amount": 250000.0,
                "advisor_fee_currency": "USD",
                "is_lead": True,
            },
        ]
    }

    investor_advisors_response = {
        "advisors": [
            {
                "advisor_company_id": 33333,
                "advisor_company_name": "Due Diligence Experts",
                "advisor_type_name": "Technical Advisor",
                "advisor_fee_amount": 50000.0,
                "advisor_fee_currency": "EUR",
                "is_lead": False,
            }
        ]
    }

    @pytest.fixture
    def add_spgi_rounds_mock_resp(self, httpx_mock: HTTPXMock) -> None:
        """Add mock response for SPGI rounds of funding."""
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundingrounds/target/{SPGI_ID_TRIPLE.company_id}",
            json=self.rounds_of_funding_response,
            is_optional=True,
        )

    @pytest.mark.asyncio
    async def test_fetch_rounds_of_funding_from_company_id(
        self,
        httpx_client: httpx.AsyncClient,
        add_spgi_rounds_mock_resp: None,
    ) -> None:
        """
        WHEN we request SPGI's rounds of funding (using SPGI's company id)
        THEN we get back SPGI's rounds of funding
        """

        resp = await fetch_rounds_of_funding_from_company_id(
            company_id=SPGI_ID_TRIPLE.company_id,
            role=RoundsOfFundingRole.company_raising_funds,
            httpx_client=httpx_client,
        )

        expected_resp = RoundsOfFundingResp.model_validate(self.rounds_of_funding_response)
        assert resp == expected_resp

    @pytest.mark.asyncio
    async def test_get_rounds_of_funding_from_identifiers(
        self,
        httpx_client: httpx.AsyncClient,
        add_spgi_rounds_mock_resp: None,
    ) -> None:
        """
        WHEN we request rounds of funding for SPGI and a non-existent company
        THEN we get back rounds of funding for SPGI and an error for the non-existent company
        """

        expected_resp = GetRoundsOfFundingFromIdentifiersResp(
            identifier_results={
                "SPGI": RoundsOfFundingResp.model_validate(self.rounds_of_funding_response)
            },
            identifier_info={"SPGI": SPGI_ID_TRIPLE},
            errors=[
                "No identification triple found for the provided identifier: NON-EXISTENT of type: ticker"
            ],
        )

        resp = await get_rounds_of_funding_from_identifiers(
            identifiers=["SPGI", "non-existent"],
            role=RoundsOfFundingRole.company_raising_funds,
            httpx_client=httpx_client,
        )

        assert resp == expected_resp

    @pytest.mark.asyncio
    async def test_get_rounds_of_funding_info_from_transaction_ids_complete_data(
        self, httpx_client: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        """
        WHEN we request funding round info for a transaction with complete advisor data
        THEN we get back complete data including advisors
        """
        transaction_id = 123456

        expected_result = GetRoundsOfFundingInfoFromTransactionIdsResp(
            results={
                123456: RoundOfFundingInfoWithAdvisors(
                    timeline=RoundOfFundingInfoTimeline(
                        announced_date="2023-01-15",
                        closed_date="2023-02-15",
                    ),
                    participants=RoundOfFundingParticipantsWithAdvisors(
                        target=CompanyIdAndNameWithAdvisors(
                            company_id=12345,
                            company_name="Target Company Inc.",
                            advisors=[
                                AdvisorResp(
                                    advisor_company_id=11111,
                                    advisor_company_name="Legal Advisors LLP",
                                    advisor_type_name="Legal Counsel",
                                    advisor_fee_amount=75000.0,
                                    advisor_fee_currency="USD",
                                    is_lead=True,
                                ),
                                AdvisorResp(
                                    advisor_company_id=22222,
                                    advisor_company_name="Investment Bank Inc",
                                    advisor_type_name="Financial Advisor",
                                    advisor_fee_amount=250000.0,
                                    advisor_fee_currency="USD",
                                    is_lead=True,
                                ),
                            ],
                        ),
                        investors=[
                            InvestorInRoundOfFundingWithAdvisors(
                                company_id=67890,
                                company_name="Investor LLC",
                                lead_investor=True,
                                investment_value=Decimal("2500000.00000000"),
                                currency="USD",
                                ownership_percentage_pre=Decimal("0.0000"),
                                ownership_percentage_post=Decimal("15.5000"),
                                board_seat_granted=True,
                                advisors=[
                                    AdvisorResp(
                                        advisor_company_id=33333,
                                        advisor_company_name="Due Diligence Experts",
                                        advisor_type_name="Technical Advisor",
                                        advisor_fee_amount=50000.0,
                                        advisor_fee_currency="EUR",
                                        is_lead=False,
                                    ),
                                ],
                            ),
                            InvestorInRoundOfFundingWithAdvisors(
                                company_id=98765,
                                company_name="Secondary Investor Corp",
                                lead_investor=False,
                                investment_value=Decimal("1000000.00000000"),
                                currency="USD",
                                ownership_percentage_pre=Decimal("0.0000"),
                                ownership_percentage_post=Decimal("6.2000"),
                                board_seat_granted=False,
                                advisors=[],
                            ),
                        ],
                    ),
                    transaction=RoundOfFundingInfoTransaction(
                        funding_type="Series A",
                        amount_offered=Decimal("5000000.00000000"),
                        currency="USD",
                        pre_money_valuation=Decimal("25000000.00000000"),
                        post_money_valuation=Decimal("30000000.00000000"),
                        use_of_proceeds="Product development and market expansion",
                        aggregate_amount_raised=Decimal("5000000.00000000"),
                    ),
                    security=RoundOfFundingInfoSecurity(
                        security_description="Series A Preferred Stock",
                        seniority_level="Senior",
                    ),
                )
            },
            errors=[],
        )

        # Mock the main funding round API call
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}",
            json=self.funding_round_response,
        )

        # Mock advisor API calls with actual advisor data
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/target",
            json=self.target_advisors_response,
        )
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/investor/{67890}",
            json=self.investor_advisors_response,
        )
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/investor/{98765}",
            json={"advisors": []},  # No advisors for this investor
        )

        result = await get_rounds_of_funding_info_from_transaction_ids(
            transaction_ids=[transaction_id],
            httpx_client=httpx_client,
        )

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_get_rounds_of_funding_info_with_mixed_advisor_data(
        self, httpx_client: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        """
        WHEN some advisor API calls return data and others return empty lists
        THEN we get advisors for some results and empty lists for others
        """
        transaction_id = 345678

        expected_result = GetRoundsOfFundingInfoFromTransactionIdsResp(
            results={
                345678: RoundOfFundingInfoWithAdvisors(
                    timeline=RoundOfFundingInfoTimeline(
                        announced_date="2023-01-15",
                        closed_date="2023-02-15",
                    ),
                    participants=RoundOfFundingParticipantsWithAdvisors(
                        target=CompanyIdAndNameWithAdvisors(
                            company_id=12345,
                            company_name="Target Company Inc.",
                            advisors=[],
                        ),
                        investors=[
                            InvestorInRoundOfFundingWithAdvisors(
                                company_id=67890,
                                company_name="Investor LLC",
                                lead_investor=True,
                                investment_value=Decimal("2500000.00000000"),
                                currency="USD",
                                ownership_percentage_pre=Decimal("0.0000"),
                                ownership_percentage_post=Decimal("15.5000"),
                                board_seat_granted=True,
                                advisors=[
                                    AdvisorResp(
                                        advisor_company_id=33333,
                                        advisor_company_name="Due Diligence Experts",
                                        advisor_type_name="Technical Advisor",
                                        advisor_fee_amount=50000.0,
                                        advisor_fee_currency="EUR",
                                        is_lead=False,
                                    ),
                                ],
                            ),
                            InvestorInRoundOfFundingWithAdvisors(
                                company_id=98765,
                                company_name="Secondary Investor Corp",
                                lead_investor=False,
                                investment_value=Decimal("1000000.00000000"),
                                currency="USD",
                                ownership_percentage_pre=Decimal("0.0000"),
                                ownership_percentage_post=Decimal("6.2000"),
                                board_seat_granted=False,
                                advisors=[],
                            ),
                        ],
                    ),
                    transaction=RoundOfFundingInfoTransaction(
                        funding_type="Series A",
                        amount_offered=Decimal("5000000.00000000"),
                        currency="USD",
                        pre_money_valuation=Decimal("25000000.00000000"),
                        post_money_valuation=Decimal("30000000.00000000"),
                        use_of_proceeds="Product development and market expansion",
                        aggregate_amount_raised=Decimal("5000000.00000000"),
                    ),
                    security=RoundOfFundingInfoSecurity(
                        security_description="Series A Preferred Stock",
                        seniority_level="Senior",
                    ),
                )
            },
            errors=[],
        )

        # Mock the main funding round API call
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}",
            json=self.funding_round_response,
        )

        # Mock advisor API calls - mixed results
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/target",
            json={"advisors": []},
        )
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/investor/{67890}",
            json=self.investor_advisors_response,  # Successful call with data
        )
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/investor/{98765}",
            json={"advisors": []},
        )

        result = await get_rounds_of_funding_info_from_transaction_ids(
            transaction_ids=[transaction_id],
            httpx_client=httpx_client,
        )

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_get_funding_summary_from_identifiers(
        self,
        httpx_client: httpx.AsyncClient,
        httpx_mock: HTTPXMock,
    ) -> None:
        """
        WHEN we request funding summary for multiple companies
        THEN we get back funding summaries with aggregate data
        """

        company_ids = [1, 2]

        # Mock the rounds of funding responses for both companies
        for company_id in company_ids:
            httpx_mock.add_response(
                method="GET",
                url=f"https://kfinance.kensho.com/api/v1/fundingrounds/target/{company_id}",
                json=self.funding_summary_rounds_response,
            )

        # Both companies share the same transaction, so its details are only fetched once.
        httpx_mock.add_response(
            method="GET",
            url="https://kfinance.kensho.com/api/v1/fundinground/info/789012",
            json=self.funding_round_response,
        )

        expected_summary = FundingSummary(
            company_id=f"C_1",  # This will be the identifier, not the company_id
            total_capital_raised=5000000.0,
            total_capital_raised_currency="USD",
            total_rounds=1,
            first_funding_date="2023-02-15",
            most_recent_funding_date="2023-02-15",
            rounds_by_type={"Series A": 1},
            sources=[
                {
                    "notes": "total_capital_raised, total_rounds, first_funding_date, most_recent_funding_date, and rounds_by_type are derived from underlying rounds of funding data that might be non-comprehensive."
                }
            ],
        )

        expected_response = GetFundingSummaryFromIdentifiersResp(
            identifier_results={
                "C_1": expected_summary,
                "C_2": expected_summary.model_copy(update={"company_id": "C_2"}),
            },
            identifier_info={"C_1": FAKE_COMPANY_1_ID_TRIPLE, "C_2": FAKE_COMPANY_2_ID_TRIPLE},
        )

        resp = await get_funding_summary_from_identifiers(
            identifiers=[f"{COMPANY_ID_PREFIX}{company_id}" for company_id in company_ids],
            httpx_client=httpx_client,
        )

        assert resp == expected_response

    @pytest.mark.asyncio
    async def test_get_rounds_of_funding_info_http_404(
        self, httpx_client: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        """
        WHEN the server returns a 404 for a non-existent transaction_id
        THEN the result contains no data for that transaction and an error is reported
        """
        transaction_id = 999999

        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}",
            status_code=404,
        )

        expected_result = GetRoundsOfFundingInfoFromTransactionIdsResp(
            results={},
            errors=[f"transaction_id {transaction_id}: No result found for {transaction_id}."],
        )

        result = await get_rounds_of_funding_info_from_transaction_ids(
            transaction_ids=[transaction_id],
            httpx_client=httpx_client,
        )

        assert result == expected_result

    @pytest.mark.asyncio
    async def test_get_rounds_of_funding_info_advisor_endpoints_404(
        self, httpx_client: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        """
        WHEN the advisor endpoints return 404
        THEN the round of funding info is returned with empty advisor lists
        """
        transaction_id = 111111

        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}",
            json=self.funding_round_response,
        )
        # Target advisors returns 404
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/target",
            status_code=404,
        )
        # Investor advisors return 404
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/investor/{67890}",
            status_code=404,
        )
        httpx_mock.add_response(
            method="GET",
            url=f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}/advisors/investor/{98765}",
            status_code=404,
        )

        expected_result = GetRoundsOfFundingInfoFromTransactionIdsResp(
            results={
                111111: RoundOfFundingInfoWithAdvisors(
                    timeline=RoundOfFundingInfoTimeline(
                        announced_date="2023-01-15",
                        closed_date="2023-02-15",
                    ),
                    participants=RoundOfFundingParticipantsWithAdvisors(
                        target=CompanyIdAndNameWithAdvisors(
                            company_id=12345,
                            company_name="Target Company Inc.",
                            advisors=[],
                        ),
                        investors=[
                            InvestorInRoundOfFundingWithAdvisors(
                                company_id=67890,
                                company_name="Investor LLC",
                                lead_investor=True,
                                investment_value=Decimal("2500000.00000000"),
                                currency="USD",
                                ownership_percentage_pre=Decimal("0.0000"),
                                ownership_percentage_post=Decimal("15.5000"),
                                board_seat_granted=True,
                                advisors=[],
                            ),
                            InvestorInRoundOfFundingWithAdvisors(
                                company_id=98765,
                                company_name="Secondary Investor Corp",
                                lead_investor=False,
                                investment_value=Decimal("1000000.00000000"),
                                currency="USD",
                                ownership_percentage_pre=Decimal("0.0000"),
                                ownership_percentage_post=Decimal("6.2000"),
                                board_seat_granted=False,
                                advisors=[],
                            ),
                        ],
                    ),
                    transaction=RoundOfFundingInfoTransaction(
                        funding_type="Series A",
                        amount_offered=Decimal("5000000.00000000"),
                        currency="USD",
                        pre_money_valuation=Decimal("25000000.00000000"),
                        post_money_valuation=Decimal("30000000.00000000"),
                        use_of_proceeds="Product development and market expansion",
                        aggregate_amount_raised=Decimal("5000000.00000000"),
                    ),
                    security=RoundOfFundingInfoSecurity(
                        security_description="Series A Preferred Stock",
                        seniority_level="Senior",
                    ),
                )
            },
            errors=[],
        )

        result = await get_rounds_of_funding_info_from_transaction_ids(
            transaction_ids=[transaction_id],
            httpx_client=httpx_client,
        )

        assert result == expected_result


class TestRoundsOfFundingRequestBounds:
    funding_round_response = TestRoundsOfFunding.funding_round_response

    def test_transaction_ids_max_length(self) -> None:
        """
        WHEN more than MAX_TOOL_LIST_ARG_LENGTH transaction_ids are passed
        THEN argument validation fails before any request is sent
        """
        GetRoundsOfFundingInfoFromTransactionIdsArgs.model_validate(
            {"transaction_ids": list(range(MAX_TOOL_LIST_ARG_LENGTH))}
        )
        with pytest.raises(ValidationError):
            GetRoundsOfFundingInfoFromTransactionIdsArgs.model_validate(
                {"transaction_ids": list(range(MAX_TOOL_LIST_ARG_LENGTH + 1))}
            )

    def test_transaction_ids_deduped(self) -> None:
        args = GetRoundsOfFundingInfoFromTransactionIdsArgs.model_validate(
            {"transaction_ids": [3, 1, 3, 2, 1]}
        )
        assert args.transaction_ids == [3, 1, 2]

    @pytest.mark.asyncio
    async def test_repeated_transaction_id_fetched_once(
        self, httpx_client: httpx.AsyncClient, httpx_mock: HTTPXMock
    ) -> None:
        """
        WHEN the same transaction_id is repeated many times
        THEN the round info and each advisor endpoint are only requested once
        """
        transaction_id = 111111
        base_url = f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}"
        httpx_mock.add_response(method="GET", url=base_url, json=self.funding_round_response)
        httpx_mock.add_response(
            method="GET", url=f"{base_url}/advisors/target", json={"advisors": []}
        )
        for investor_id in (67890, 98765):
            httpx_mock.add_response(
                method="GET",
                url=f"{base_url}/advisors/investor/{investor_id}",
                json={"advisors": []},
            )

        result = await get_rounds_of_funding_info_from_transaction_ids(
            transaction_ids=[transaction_id] * 10_000,
            httpx_client=httpx_client,
        )

        assert list(result.results) == [transaction_id]
        assert result.errors == []
        assert len(httpx_mock.get_requests()) == 4

    @pytest.mark.asyncio
    async def test_advisor_requests_capped(
        self,
        httpx_client: httpx.AsyncClient,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        WHEN a round has more participants than MAX_BATCH_TASKS
        THEN only MAX_BATCH_TASKS advisor requests are sent and an error explains the cut
        """
        monkeypatch.setattr(rounds_of_funding_tools, "MAX_BATCH_TASKS", 2)
        transaction_id = 111111
        base_url = f"https://kfinance.kensho.com/api/v1/fundinground/info/{transaction_id}"
        httpx_mock.add_response(method="GET", url=base_url, json=self.funding_round_response)
        httpx_mock.add_response(
            method="GET", url=f"{base_url}/advisors/target", json={"advisors": []}
        )
        httpx_mock.add_response(
            method="GET", url=f"{base_url}/advisors/investor/67890", json={"advisors": []}
        )

        result = await get_rounds_of_funding_info_from_transaction_ids(
            transaction_ids=[transaction_id], httpx_client=httpx_client
        )

        assert result.errors == ["Advisors were only fetched for the first 2 of 3 participants."]
        assert len(httpx_mock.get_requests()) == 3

    @pytest.mark.asyncio
    async def test_funding_summary_detail_requests_capped(
        self,
        httpx_client: httpx.AsyncClient,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        WHEN the resolved companies have more rounds than MAX_BATCH_TASKS
        THEN round details are fetched newest first, only until each company has a total,
            and at most MAX_BATCH_TASKS detail requests are sent
        """
        monkeypatch.setattr(rounds_of_funding_tools, "MAX_BATCH_TASKS", 3)

        def rounds(*transaction_ids_and_dates: tuple[int, str | None]) -> dict:
            return {
                "rounds_of_funding": [
                    {
                        "transaction_id": transaction_id,
                        "funding_round_notes": None,
                        "closed_date": closed_date,
                        "funding_type": "Series A",
                    }
                    for transaction_id, closed_date in transaction_ids_and_dates
                ]
            }

        httpx_mock.add_response(
            method="GET",
            url="https://kfinance.kensho.com/api/v1/fundingrounds/target/1",
            json=rounds((1, "2020-01-01"), (2, "2023-01-01"), (3, None)),
        )
        httpx_mock.add_response(
            method="GET",
            url="https://kfinance.kensho.com/api/v1/fundingrounds/target/2",
            json=rounds((4, "2022-01-01")),
        )
        round_without_total = deepcopy(self.funding_round_response)
        round_without_total["transaction"]["aggregate_amount_raised"] = None
        # First wave: the most recent round of each company. Round 2 has no total.
        httpx_mock.add_response(
            method="GET",
            url="https://kfinance.kensho.com/api/v1/fundinground/info/2",
            json=round_without_total,
        )
        httpx_mock.add_response(
            method="GET",
            url="https://kfinance.kensho.com/api/v1/fundinground/info/4",
            json=self.funding_round_response,
        )
        # Second wave: the next round of C_1, which still has no total. Round 3 is never fetched.
        httpx_mock.add_response(
            method="GET",
            url="https://kfinance.kensho.com/api/v1/fundinground/info/1",
            json=self.funding_round_response,
        )

        resp = await get_funding_summary_from_identifiers(
            identifiers=["C_1", "C_2"], httpx_client=httpx_client
        )

        assert resp.errors == ["Round details were only fetched for 3 of 4 rounds."]
        assert resp.identifier_results["C_1"].total_rounds == 3
        assert resp.identifier_results["C_1"].total_capital_raised == 5000000.0
        assert resp.identifier_results["C_2"].total_capital_raised == 5000000.0
        detail_requests = sorted(
            request.url.path
            for request in httpx_mock.get_requests()
            if "/fundinground/info/" in request.url.path
        )
        assert detail_requests == [
            "/api/v1/fundinground/info/1",
            "/api/v1/fundinground/info/2",
            "/api/v1/fundinground/info/4",
        ]


@pytest.mark.asyncio
async def test_batch_execute_rejects_oversized_batches() -> None:
    """
    WHEN more than MAX_BATCH_TASKS tasks are passed to batch_execute_async_tasks
    THEN it raises without running any task
    """

    calls = []

    async def record() -> None:
        calls.append(1)

    tasks = [AsyncTask(func=record, result_key=i) for i in range(MAX_BATCH_TASKS + 1)]
    with pytest.raises(ValueError, match="maximum is"):
        await batch_execute_async_tasks(tasks=tasks)
    assert calls == []
