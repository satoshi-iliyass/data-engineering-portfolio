-- Customer dimension enriched with lifetime metrics (completed/shipped orders only)
with valid_orders as (
    select o.customer_id, o.order_id, o.order_date, sum(i.line_amount) as amount
    from {{ ref('stg_orders') }} o
    join {{ ref('stg_order_items') }} i using (order_id)
    where o.status in ('completed', 'shipped')
    group by 1, 2, 3
)
select
    c.customer_id,
    c.customer_name,
    c.email,
    c.country_code,
    c.signup_date,
    count(v.order_id)                         as lifetime_orders,
    coalesce(sum(v.amount), 0)                as lifetime_revenue,
    min(v.order_date)                         as first_order_date,
    max(v.order_date)                         as last_order_date,
    case
        when coalesce(sum(v.amount), 0) >= 50000 then 'gold'
        when coalesce(sum(v.amount), 0) >= 30000 then 'silver'
        else 'bronze'
    end                                       as segment
from {{ ref('stg_customers') }} c
left join valid_orders v using (customer_id)
group by all
