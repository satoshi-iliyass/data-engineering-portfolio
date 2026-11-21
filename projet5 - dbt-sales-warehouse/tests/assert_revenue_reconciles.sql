-- Revenue in the monthly mart must equal revenue in the fact table (no rows lost or doubled).
-- dbt fails the test if this query returns any row.
with mart as (select sum(revenue) as r from {{ ref('mart_monthly_revenue') }}),
     fact as (select sum(line_amount) as r from {{ ref('fct_order_items') }} where is_revenue)
select mart.r, fact.r
from mart, fact
where abs(mart.r - fact.r) > 0.01
