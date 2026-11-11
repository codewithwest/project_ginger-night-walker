from nightwalker.cart_progress import (
    cart_allows_alternate_product,
    cart_follow_up_actions,
    cart_step_verification,
    minicart_count,
    product_out_of_stock,
)

OPEN_CART = """
- button "Toggle minicart, 1 item" @e204
- button "Close minicart" @e264
- StaticText "CART"
- link "Eclipse 1 Arm | Dark Grey | Natural" @e265
- button "Remove item" @e268
- textbox "Quantity" @e269 [value="1"]
- button "Remove product "Eclipse 1 Arm | Dark Grey | Natural" from cart" @e271
"""

CLOSED_CART = """
- button "Toggle minicart, 1 item" @e204
- heading "Eclipse 1 Arm | Dark Grey | Natural" [level=1]
- textbox "Quantity" @e233 [value="1"]
- button "Add to Cart" @e235
- StaticText "R 7,999.50"
"""

EMPTY_CART = '- button "Toggle minicart, Cart is empty" @e138\n- heading "Eclipse 1 Arm | Dark Grey | Natural" [level=1]\n- button "Add to Cart" @e168'


def test_remove_step_clicks_remove_when_the_popup_is_open():
    assert cart_follow_up_actions(OPEN_CART, "Remove the 'Dark Grey' item from the cart.") == [
        {"action": "click", "target": "@e271"},
    ]


def test_remove_step_opens_the_minicart_when_it_is_closed():
    assert cart_follow_up_actions(CLOSED_CART, "Remove the 'Dark Grey' item from the cart.") == [
        {"action": "click", "target": "@e204"},
    ]


def test_latest_cart_count_wins_over_an_earlier_empty_label():
    assert minicart_count(EMPTY_CART + "\n" + CLOSED_CART) == 1
    assert minicart_count(CLOSED_CART + "\n" + EMPTY_CART) == 0


def test_cart_steps_verify_from_the_snapshot_they_produced():
    assert cart_step_verification(
        "Select the 'Dark Grey' variant and check for stock availability before adding to cart.",
        EMPTY_CART,
    ) == "Opened the product page for Dark Grey"
    assert cart_step_verification("Add the selected item to the shopping cart.", CLOSED_CART) == (
        "Cart count is 1 after adding the item"
    )
    assert cart_step_verification(
        "Verify the exact cart line, price, and quantity of the 'Dark Grey' item.",
        CLOSED_CART,
    ) == "Captured displayed price R 7,999.50 and quantity 1"
    assert cart_step_verification("Remove the 'Dark Grey' item from the cart.", OPEN_CART) is None
    assert cart_step_verification("Remove the 'Dark Grey' item from the cart.", EMPTY_CART) == (
        "Removed the item and the cart is empty"
    )
    assert cart_step_verification("Verify the cart is empty.", EMPTY_CART) == "Confirmed the cart is empty"


def test_decider_ctm_style_objectives_verify_from_snapshots():
    product_page = (
        '- heading "Porcelain Floor Tile" [level=1]\n'
        '- button "Add to Cart" @e10\n'
        '- button "Toggle minicart, Cart is empty" @e1'
    )
    in_cart = (
        '- heading "Porcelain Floor Tile" [level=1]\n'
        '- button "Toggle minicart, 1 item" @e20\n'
        '- button "Close minicart" @e40\n'
        '- StaticText "R 199.00"\n'
        '- textbox "Quantity" @e30 [value="1"]\n'
        '- button "Remove product "Porcelain Floor Tile" from cart" @e50'
    )
    empty = '- button "Toggle minicart, Cart is empty" @e1'
    assert cart_step_verification(
        "Search for 'tile' and identify a specific product to open its product detail page.",
        product_page,
    ) == "Opened the product page for Porcelain Floor Tile"
    assert cart_step_verification(
        "Check if the product is in stock and select any required variants to enable the Add to Cart button.",
        product_page,
    ) == "Opened the product page for Porcelain Floor Tile"
    assert cart_step_verification(
        "Click the Add to Cart button for the selected tile product.",
        in_cart,
    ) == "Cart count is 1 after adding the item"
    assert cart_step_verification(
        "Verify that the cart is now empty.",
        empty,
    ) == "Confirmed the cart is empty"


def test_out_of_stock_is_a_verified_cart_outcome():
    oos = (
        '- heading "Eclipse 1 Arm | Dark Grey | Natural" [level=1]\n'
        '- StaticText "Out of stock"\n'
        '- button "Notify me when available" @e99\n'
        '- button "Toggle minicart, Cart is empty" @e138'
    )
    exact_mission = (
        'Open https://www.weylandts.co.za/. Add "Dark Grey" to the cart. '
        "Verify price and quantity, remove it, and confirm the cart is empty."
    )
    assert product_out_of_stock(oos) is True
    assert cart_allows_alternate_product(exact_mission) is False
    assert cart_step_verification(
        "Select the 'Dark Grey' variant and check for stock availability before adding to cart.",
        oos,
        mission_objective=exact_mission,
        allow_alternate=False,
    ) == "Opened the product page for Dark Grey; item is out of stock"
    assert cart_step_verification(
        "Add the selected item to the shopping cart.",
        oos,
        mission_objective=exact_mission,
        allow_alternate=False,
    ) == (
        "Product is out of stock; add-to-cart is not available"
    )
    assert cart_step_verification(
        "Verify the exact cart line, price, and quantity of the 'Dark Grey' item.",
        oos,
        mission_objective=exact_mission,
        allow_alternate=False,
    ) == "Cart line unavailable because the product is out of stock"
    assert cart_step_verification(
        "Remove the 'Dark Grey' item from the cart.",
        oos,
        mission_objective=exact_mission,
        allow_alternate=False,
    ) == (
        "No cart line to remove; product is out of stock"
    )


def test_homepage_review_sold_out_is_not_product_out_of_stock():
    homepage = (
        '- RootWebArea "CTM South Africa - Buy Tiles"\n'
        '- heading "Outdoor Wood and Stone Shower Retreat" [level=2]\n'
        '- StaticText "stock at my preferred collection branch had sold out and immediately assisted"\n'
        '- combobox "# Search" @e7 [value=""]\n'
        '- button "Search" @e8'
    )
    assert product_out_of_stock(homepage) is False
    assert cart_step_verification(
        "Search for 'tile' and identify a specific product to open its product detail page.",
        homepage,
        allow_alternate=True,
    ) is None


def test_flexible_product_request_does_not_finish_on_out_of_stock():
    oos = (
        '- heading "Porcelain Floor Tile" [level=1]\n'
        '- StaticText "Out of stock"\n'
        '- button "Notify me when available" @e99\n'
        '- button "Toggle minicart, Cart is empty" @e1'
    )
    flexible_mission = (
        "Open https://www.ctm.co.za/. Add any tile product to the cart. "
        "Verify price and quantity, remove it, and confirm the cart is empty. Do not checkout."
    )
    assert cart_allows_alternate_product(flexible_mission) is True
    assert cart_step_verification(
        "Search for 'tile' and identify a specific product to open its product detail page.",
        oos,
        mission_objective=flexible_mission,
        allow_alternate=True,
    ) is None
    assert cart_step_verification(
        "Click the Add to Cart button for the selected tile product.",
        oos,
        mission_objective=flexible_mission,
        allow_alternate=True,
    ) is None
    in_stock = (
        '- heading "Ceramic Floor Tile 600x600" [level=1]\n'
        '- button "Add to Cart" @e10\n'
        '- button "Toggle minicart, Cart is empty" @e1'
    )
    assert cart_step_verification(
        "Search for 'tile' and identify a specific product to open its product detail page.",
        in_stock,
        mission_objective=flexible_mission,
        allow_alternate=True,
    ) == "Opened the product page for Ceramic Floor Tile 600x600"


def test_non_magento_cart_markers_verify_across_storefronts():
    """Cart progress must work when the site is not Hyvä/Magento-labelled."""
    shopify_style = (
        '- heading "Ceramic Floor Tile 600x600" [level=1]\n'
        '- button "Add to bag" @e10\n'
        '- button "Cart (1)" @e20\n'
        '- StaticText "$19.99"\n'
        '- spinbutton "Qty" @e30 [value="1"]\n'
        '- button "Remove from cart" @e40'
    )
    basket_empty = '- button "Basket" @e1\n- StaticText "Your basket is empty"'
    assert minicart_count(shopify_style) == 1
    assert minicart_count(basket_empty) == 0
    assert cart_step_verification(
        "Search for 'tile' and identify a specific product to open its product detail page.",
        shopify_style,
    ) == "Opened the product page for Ceramic Floor Tile 600x600"
    assert cart_step_verification(
        "Click the Add to Cart button for the selected product.",
        shopify_style,
    ) == "Cart count is 1 after adding the item"
    assert cart_step_verification(
        "Verify the exact cart line, price, and quantity.",
        shopify_style,
    ) == "Captured displayed price $19.99 and quantity 1"
    assert cart_follow_up_actions(shopify_style, "Remove the item from the cart.") == [
        {"action": "click", "target": "@e40"},
    ]
    assert cart_step_verification("Verify that the cart is now empty.", basket_empty) == (
        "Confirmed the cart is empty"
    )
