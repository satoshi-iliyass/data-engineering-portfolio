-- Business-ready monthly KPIs by category
select
    date_trunc('month', f.order_date)              as month,
    p.category,
    count(distinct f.order_id)                     as orders,
    sum(f.quantity)                                as units,
    sum(f.line_amount)                             as revenue,
    sum(f.discount_amount)                         as discounts,
    round(sum(f.line_amount) / count(distinct f.order_id), 2) as avg_order_value
from {{ ref('fct_order_items') }} f
join {{ ref('dim_products') }} p using (product_id)
where f.is_revenue
group by all
order by month, category
