import asyncio
import io
import logging
import random
import time
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from bd_models.models import BallInstance  # type: ignore
from ballsdex.core.utils.transformers import (  # type: ignore
    BallInstanceTransform,
    SpecialEnabledTransform,
)
from settings.models import settings  # type: ignore

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.battle")

@dataclass
class BattleBall:
    name: str
    owner: str
    health: int
    attack: int
    health_bonus: int
    attack_bonus: int
    emoji: discord.Emoji | str = ""
    dead: bool = False


@dataclass
class BattleInstance:
    p1_balls: list[BattleBall] = field(default_factory=list)
    p2_balls: list[BattleBall] = field(default_factory=list)
    winner: str = ""
    turns: int = 0


@dataclass
class GuildBattle:
    interaction: discord.Interaction
    author: discord.Member
    opponent: discord.Member
    battle: BattleInstance = field(default_factory=BattleInstance)
    author_ready: bool = False
    opponent_ready: bool = False
    author_confirmed: bool = False
    opponent_confirmed: bool = False
    announce_mention: str = ""
    battle_message: discord.Message | None = None
    current_view: discord.ui.LayoutView | None = None
    amount_required: int = 3
    allow_duplicates: bool = True
    allow_buffs: bool = True
    created_at: float = field(default_factory=time.time)


battles: list[GuildBattle] = []


def fetch_battle(user: discord.User | discord.Member) -> GuildBattle | None:
    for battle in battles:
        if user in (battle.author, battle.opponent):
            return battle
    return None


def get_damage(ball: BattleBall) -> int:
    return int(ball.attack * random.uniform(0.8, 1.2))


def attack_ball(current_ball: BattleBall, enemy_balls: list[BattleBall]) -> str:
    alive_balls = [b for b in enemy_balls if not b.dead]
    enemy = random.choice(alive_balls)
    damage = get_damage(current_ball)
    enemy.health -= damage
    if enemy.health <= 0:
        enemy.health = 0
        enemy.dead = True
    if enemy.dead:
        return (
            f"{current_ball.owner}'s {current_ball.name} "
            f"has killed {enemy.owner}'s {enemy.name}"
        )
    return (
        f"{current_ball.owner}'s {current_ball.name} "
        f"has dealt {damage} damage to {enemy.owner}'s {enemy.name}"
    )


def random_event() -> bool:
    return random.randint(0, 100) <= 30


def gen_battle(battle: BattleInstance) -> Generator[str, None, None]:
    turn = 0
    p1_name = battle.p1_balls[0].owner if battle.p1_balls else "Player1"
    p2_name = battle.p2_balls[0].owner if battle.p2_balls else "Player2"

    yield f"Battle between {p1_name} and {p2_name} begins! - {p1_name} begins"

    if all(ball.attack <= 0 for ball in battle.p1_balls + battle.p2_balls):
        yield "Everyone stared at each other, resulting in nobody winning."
        return

    while any(not b.dead for b in battle.p1_balls) and any(
        not b.dead for b in battle.p2_balls
    ):
        alive_p1 = [b for b in battle.p1_balls if not b.dead]
        alive_p2 = [b for b in battle.p2_balls if not b.dead]

        for p1_ball, p2_ball in zip(alive_p1, alive_p2):
            if not p1_ball.dead:
                turn += 1
                if random_event():
                    yield (
                        f"Turn {turn}: {p1_ball.owner}'s {p1_ball.name} "
                        f"missed {p2_ball.owner}'s {p2_ball.name}"
                    )
                else:
                    yield f"Turn {turn}: {attack_ball(p1_ball, battle.p2_balls)}"
                if all(b.dead for b in battle.p2_balls):
                    break

            if not p2_ball.dead:
                turn += 1
                if random_event():
                    yield (
                        f"Turn {turn}: {p2_ball.owner}'s {p2_ball.name} "
                        f"missed {p1_ball.owner}'s {p1_ball.name}"
                    )
                else:
                    yield f"Turn {turn}: {attack_ball(p2_ball, battle.p1_balls)}"
                if all(b.dead for b in battle.p1_balls):
                    break

    if all(b.dead for b in battle.p1_balls):
        battle.winner = battle.p2_balls[0].owner
    elif all(b.dead for b in battle.p2_balls):
        battle.winner = battle.p1_balls[0].owner
    else:
        p1_hp = sum(b.health for b in battle.p1_balls if not b.dead)
        p2_hp = sum(b.health for b in battle.p2_balls if not b.dead)
        if p1_hp > p2_hp:
            battle.winner = battle.p1_balls[0].owner
        elif p2_hp > p1_hp:
            battle.winner = battle.p2_balls[0].owner
        else:
            battle.winner = "Draw"

    battle.turns = turn


def gen_deck(balls: list[BattleBall], strikethrough: bool = False) -> str:
    if not balls:
        return "*Empty*"
    lines: list[str] = []
    for ball in balls:
        emoji_str = f"{ball.emoji} " if ball.emoji else ""
        stats = f"ATK:{ball.attack_bonus:+}% HP:{ball.health_bonus:+}%"
        if strikethrough:
            lines.append(f"- ~~{emoji_str}{ball.name} {stats}~~")
        else:
            lines.append(f"- {emoji_str}{ball.name} {stats}")
    deck = "\n".join(lines)
    if len(deck) > 1024:
        return (
            deck[:951]
            + "\n<truncated due to discord limits, rest of your balls are still here>"
        )
    return deck


def _cancelled_container(
    gb: GuildBattle,
    cancelled_by: discord.User | discord.Member,
) -> discord.ui.Container:
    author_name = (
        f"🚫 {gb.author.name}" if cancelled_by == gb.author else gb.author.name
    )
    opponent_name = (
        f"🚫 {gb.opponent.name}" if cancelled_by == gb.opponent else gb.opponent.name
    )
    return discord.ui.Container(
        discord.ui.TextDisplay(
            f"## {settings.plural_collectible_name.title()} Battle Plan"
        ),
        discord.ui.TextDisplay("**The battle has been cancelled.**"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            f"**{author_name}**\n"
            f"{gen_deck(gb.battle.p1_balls, strikethrough=bool(gb.battle.p1_balls))}"
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            f"**{opponent_name}**\n"
            f"{gen_deck(gb.battle.p2_balls, strikethrough=bool(gb.battle.p2_balls))}"
        ),
        accent_colour=discord.Colour(0xE74D3C),
    )


def _timeout_container(gb: GuildBattle) -> discord.ui.Container:
    return discord.ui.Container(
        discord.ui.TextDisplay(
            f"## {settings.plural_collectible_name.title()} Battle Plan"
        ),
        discord.ui.TextDisplay("The battle timed out"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            f"**{gb.author.name}'s deck:**\n{gen_deck(gb.battle.p1_balls)}"
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            f"**{gb.opponent.name}'s deck:**\n{gen_deck(gb.battle.p2_balls)}"
        ),
        accent_colour=discord.Colour(0x992E22),
    )


def _disabled_setup_row() -> discord.ui.ActionRow:
    return discord.ui.ActionRow(
        discord.ui.Button(
            style=discord.ButtonStyle.primary,
            emoji="🔒",
            label="Lock proposal",
            disabled=True,
        ),
        discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            emoji="💨",
            label="Reset",
            disabled=True,
        ),
        discord.ui.Button(
            style=discord.ButtonStyle.danger,
            emoji="✖️",
            label="Cancel battle",
            disabled=True,
        ),
    )


def _disabled_ready_row() -> discord.ui.ActionRow:
    return discord.ui.ActionRow(
        discord.ui.Button(style=discord.ButtonStyle.success, emoji="✔️", disabled=True),
        discord.ui.Button(style=discord.ButtonStyle.danger, emoji="✖️", disabled=True),
    )


def _make_terminal_view(
    container: discord.ui.Container,
    *,
    ready_buttons: bool = False,
    mention_text: str = "",
) -> discord.ui.LayoutView:
    """Creates a non-interactive LayoutView for terminal battle states."""
    view = discord.ui.LayoutView(timeout=None)
    if mention_text:
        view.add_item(discord.ui.TextDisplay(mention_text))
    view.add_item(container)
    view.add_item(_disabled_ready_row() if ready_buttons else _disabled_setup_row())
    return view


class BattleSetupContainer(discord.ui.Container):
    """Interactive setup container — lock, reset, and cancel buttons."""

    _row = discord.ui.ActionRow()

    def __init__(self, gb: GuildBattle) -> None:
        self._gb = gb
        author_prefix = "🔒 " if gb.author_ready else ""
        opponent_prefix = "🔒 " if gb.opponent_ready else ""
        body = (
            f"Add or remove {settings.plural_collectible_name} you want to propose "
            f"using `/battle add` and `/battle remove` commands.\n"
            f"Once you're finished, click the lock button to confirm your proposal.\n"
            f"*You have 15 minutes before this interaction ends.*\n\n"
            f"**Settings**\n"
            f"• Duplicates: {'Allowed' if gb.allow_duplicates else 'Not allowed'}\n"
            f"• Buffs: {'Allowed' if gb.allow_buffs else 'Not allowed'}\n"
            f"• Amount: {gb.amount_required}"
        )
        super().__init__(
            discord.ui.TextDisplay(
                f"## {settings.plural_collectible_name.title()} Battle Plan"
            ),
            discord.ui.TextDisplay(body),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"**{author_prefix}{gb.author.name}**\n{gen_deck(gb.battle.p1_balls)}"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"**{opponent_prefix}{gb.opponent.name}**\n{gen_deck(gb.battle.p2_balls)}"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay("-# This message is updated every 15 seconds."),
            accent_colour=discord.Colour.blurple(),
        )

    def to_components(self) -> list[dict[str, Any]]:
        # Render text content before the interactive action row
        rows = [i for i in self._children if i is self._row]
        non_rows = [i for i in self._children if i is not self._row]
        return [i.to_component_dict() for i in non_rows + rows]

    @_row.button(style=discord.ButtonStyle.primary, emoji="🔒", label="Lock proposal")
    async def lock_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        gb = self._gb
        user_balls = (
            gb.battle.p1_balls if interaction.user == gb.author else gb.battle.p2_balls
        )

        if len(user_balls) < gb.amount_required:
            await interaction.response.send_message(
                f"You need to have exactly {gb.amount_required} "
                f"{settings.plural_collectible_name} in your proposal to lock it.",
                ephemeral=True,
            )
            return

        if interaction.user == gb.author:
            gb.author_ready = True
        elif interaction.user == gb.opponent:
            gb.opponent_ready = True

        if gb.author_ready and gb.opponent_ready:
            if (
                len(gb.battle.p1_balls) < gb.amount_required
                or len(gb.battle.p2_balls) < gb.amount_required
            ):
                await interaction.response.send_message(
                    f"Both users must add at least {gb.amount_required} "
                    f"{settings.plural_collectible_name}!",
                    ephemeral=True,
                )
                return

            new_view = BothLockedView(
                gb,
                mention_text=f"{gb.author.mention} vs {gb.opponent.mention}",
            )
            gb.current_view = new_view

            await interaction.response.defer()
            if interaction.message is None:
                return
            await interaction.message.edit(
                view=new_view,
                attachments=[],
            )
        else:
            await interaction.response.send_message(
                "Done! Waiting for the other player to press 'Ready'.", ephemeral=True
            )
            if gb.battle_message:
                new_view = BattleSetupView(gb, announce_mention=gb.announce_mention)
                gb.current_view = new_view
                await gb.battle_message.edit(view=new_view)

    @_row.button(style=discord.ButtonStyle.secondary, emoji="💨", label="Reset")
    async def reset_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        gb = self._gb
        user_balls = (
            gb.battle.p1_balls if interaction.user == gb.author else gb.battle.p2_balls
        )
        user_balls.clear()

        if gb.battle_message:
            new_view = BattleSetupView(gb, announce_mention=gb.announce_mention)
            gb.current_view = new_view
            await gb.battle_message.edit(view=new_view)

        await interaction.response.send_message(
            "Your countryballs have been reset!", ephemeral=True
        )

    @_row.button(style=discord.ButtonStyle.danger, emoji="✖️", label="Cancel battle")
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        gb = self._gb
        terminal_view = _make_terminal_view(
            _cancelled_container(gb, interaction.user), ready_buttons=False
        )

        if gb.battle_message:
            await gb.battle_message.edit(view=terminal_view)

        try:
            await interaction.response.send_message(
                "Battle has been cancelled.", ephemeral=True
            )
        except discord.errors.InteractionResponded:
            pass

        battles.pop(battles.index(gb))
        gb.battle_message = None


class BothLockedContainer(discord.ui.Container):
    """Both-locked container — execute and cancel buttons."""

    _row = discord.ui.ActionRow()

    def __init__(self, gb: GuildBattle) -> None:
        self._gb = gb
        author_prefix = "✅ " if gb.author_confirmed else "🔒 "
        opponent_prefix = "✅ " if gb.opponent_confirmed else "🔒 "
        if gb.author_confirmed and gb.opponent_confirmed:
            status = "Both players confirmed! Starting battle..."
        elif gb.author_confirmed or gb.opponent_confirmed:
            status = "Waiting for the other player to confirm..."
        else:
            status = "Both users have locked their propositions! Now confirm to begin the battle."
        super().__init__(
            discord.ui.TextDisplay(
                f"## {settings.plural_collectible_name.title()} Battle Plan"
            ),
            discord.ui.TextDisplay(status),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"**{author_prefix}{gb.author.name}**\n{gen_deck(gb.battle.p1_balls)}"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                f"**{opponent_prefix}{gb.opponent.name}**\n{gen_deck(gb.battle.p2_balls)}"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay("-# This message is updated every 15 seconds."),
            accent_colour=discord.Colour(0xFEE65C),
        )

    def to_components(self) -> list[dict[str, Any]]:
        # Render text content before the interactive action row
        rows = [i for i in self._children if i is self._row]
        non_rows = [i for i in self._children if i is not self._row]
        return [i.to_component_dict() for i in non_rows + rows]

    @_row.button(style=discord.ButtonStyle.success, emoji="✔️")
    async def execute_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        gb = self._gb

        if interaction.user == gb.author:
            gb.author_confirmed = True
        elif interaction.user == gb.opponent:
            gb.opponent_confirmed = True

        if not (gb.author_confirmed and gb.opponent_confirmed):
            new_view = BothLockedView(gb)
            gb.current_view = new_view
            await interaction.response.defer()
            if gb.battle_message:
                await gb.battle_message.edit(view=new_view)
            return

        battle_log = "\n".join(gen_battle(gb.battle))

        concluded_view = _make_terminal_view(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## {settings.plural_collectible_name.title()} Battle Plan"
                ),
                discord.ui.TextDisplay("Battle Plan concluded!"),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    f"**✅ {gb.author.name}**\n{gen_deck(gb.battle.p1_balls)}"
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    f"**✅ {gb.opponent.name}**\n{gen_deck(gb.battle.p2_balls)}"
                ),
                accent_colour=discord.Colour.green(),
            ),
            ready_buttons=True,
            mention_text=f"{gb.author.mention} vs {gb.opponent.mention}",
        )

        logs_view = discord.ui.LayoutView(timeout=None)
        logs_view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(
                    f"## Battle between {gb.author.name} and {gb.opponent.name}"
                ),
                discord.ui.TextDisplay(
                    f"**Battle settings**\n"
                    f"Duplicates: {'Allowed' if gb.allow_duplicates else 'Not allowed'}\n"
                    f"Buffs: {'Allowed' if gb.allow_buffs else 'Not allowed'}\n"
                    f"Amount: {gb.amount_required}"
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    f"**{gb.author.name}'s deck:**\n{gen_deck(gb.battle.p1_balls)}"
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    f"**{gb.opponent.name}'s deck:**\n{gen_deck(gb.battle.p2_balls)}"
                ),
                discord.ui.Separator(),
                discord.ui.TextDisplay(
                    f"**Winner:** {gb.battle.winner} — Turn {gb.battle.turns}"
                ),
                discord.ui.TextDisplay("-# Battle log is attached."),
                accent_colour=discord.Colour.blurple(),
            )
        )

        await interaction.response.defer()
        if interaction.message is None:
            return
        await interaction.message.edit(
            view=concluded_view,
            attachments=[],
        )

        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            return
        await channel.send(view=logs_view)
        await channel.send(
            file=discord.File(
                io.BytesIO(battle_log.encode()), filename="battle-log.txt"
            ),
        )

        battles.pop(battles.index(gb))
        gb.battle_message = None

    @_row.button(style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        gb = self._gb
        terminal_view = _make_terminal_view(
            _cancelled_container(gb, interaction.user), ready_buttons=True
        )

        if gb.battle_message:
            await gb.battle_message.edit(view=terminal_view)

        try:
            await interaction.response.send_message(
                "Battle has been cancelled.", ephemeral=True
            )
        except discord.errors.InteractionResponded:
            pass

        battles.pop(battles.index(gb))
        gb.battle_message = None


class BattleSetupView(discord.ui.LayoutView):
    """Active battle setup view."""

    def __init__(self, gb: GuildBattle, *, announce_mention: str = "") -> None:
        super().__init__(timeout=None)
        self._gb = gb
        if announce_mention:
            self.add_item(discord.ui.TextDisplay(announce_mention))
        self.add_item(BattleSetupContainer(gb))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self._gb.author, self._gb.opponent):
            await interaction.response.send_message(
                "You cannot interact with this battle!", ephemeral=True
            )
            return False
        return True


class BothLockedView(discord.ui.LayoutView):
    """Both-locked state view."""

    def __init__(self, gb: GuildBattle, *, mention_text: str = "") -> None:
        super().__init__(timeout=None)
        self._gb = gb
        if mention_text:
            self.add_item(discord.ui.TextDisplay(mention_text))
        self.add_item(BothLockedContainer(gb))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self._gb.author, self._gb.opponent):
            await interaction.response.send_message(
                "You cannot interact with this battle!", ephemeral=True
            )
            return False
        return True


class Battle(commands.GroupCog):
    """Battle your countryballs!"""

    def __init__(self, bot: "BallsDexBot") -> None:
        self.bot = bot
        asyncio.create_task(self._battle_expiration_checker())

    async def _battle_expiration_checker(self) -> None:
        """Removes expired battles (>15 min) every minute."""
        while True:
            try:
                await asyncio.sleep(60)
                current_time = time.time()
                expired = [b for b in battles if current_time - b.created_at > 900]
                for gb in expired:
                    try:
                        if gb.battle_message:
                            if gb.author_ready and gb.opponent_ready:
                                timeout_view = _make_terminal_view(
                                    _timeout_container(gb), ready_buttons=True
                                )
                            else:
                                timeout_view = _make_terminal_view(
                                    _timeout_container(gb), ready_buttons=False
                                )
                            await gb.battle_message.edit(view=timeout_view)

                        if gb in battles:
                            battles.remove(gb)
                        gb.battle_message = None

                    except Exception:
                        log.exception("Error handling expired battle")
                        if gb in battles:
                            battles.remove(gb)
                        gb.battle_message = None

            except Exception:
                log.exception("Battle expiration checker error")
                await asyncio.sleep(60)

    @app_commands.command()
    async def start(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duplicates: bool = True,
        buffs: bool = True,
        amount: int = 3,
    ) -> None:
        """
        Begin a battle with the chosen user.

        Parameters
        ----------
        user: discord.Member
            The user you want to battle with
        duplicates: bool
            Whether or not you want to allow duplicates in your battle
        buffs: bool
            Whether or not you want to allow buffs in your battle
        amount: int
            The amount of countryballs needed for the battle. Minimum is 3, maximum is 10
        """
        if user.bot:
            await interaction.response.send_message(
                "You can't battle against bots.", ephemeral=True
            )
            return

        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't battle against yourself.", ephemeral=True
            )
            return

        if fetch_battle(user) is not None:
            await interaction.response.send_message(
                "That user is already in a battle.", ephemeral=True
            )
            return

        if fetch_battle(interaction.user) is not None:
            await interaction.response.send_message(
                "You are already in a battle.", ephemeral=True
            )
            return

        if amount < 3 or amount > 10:
            await interaction.response.send_message(
                "Amount must be between 3 and 10 countryballs!", ephemeral=True
            )
            return

        assert isinstance(interaction.user, discord.Member)
        gb = GuildBattle(
            interaction,
            interaction.user,
            user,
            amount_required=amount,
            allow_duplicates=duplicates,
            allow_buffs=buffs,
        )
        gb.announce_mention = (
            f"Hey, {user.mention}, {interaction.user.name} "
            f"is proposing a battle with you!"
        )
        battles.append(gb)

        view = BattleSetupView(gb, announce_mention=gb.announce_mention)
        gb.current_view = view

        await interaction.response.send_message(
            "The battle has started!", ephemeral=True
        )

        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            battles.remove(gb)
            return

        battle_message = await channel.send(view=view)

        gb.battle_message = battle_message
        asyncio.create_task(self._update_battle_message(gb))

    async def _update_battle_message(self, gb: GuildBattle) -> None:
        """Refreshes the battle message every 15 seconds to keep deck lists current."""
        while gb in battles and gb.battle_message:
            try:
                await asyncio.sleep(15)
                if (
                    gb in battles
                    and gb.battle_message
                    and isinstance(gb.current_view, BattleSetupView)
                ):
                    new_view = BattleSetupView(gb, announce_mention=gb.announce_mention)
                    gb.current_view = new_view
                    await gb.battle_message.edit(view=new_view)
            except (discord.NotFound, discord.Forbidden):
                break
            except Exception:
                log.exception("Error updating battle message")
                break

    async def add_balls(
        self,
        interaction: discord.Interaction,
        countryballs: list[BallInstance],
    ) -> AsyncGenerator[bool, None]:
        gb = fetch_battle(interaction.user)

        if gb is None:
            await interaction.response.send_message(
                "You aren't a part of a battle!", ephemeral=True
            )
            return

        if interaction.guild_id != gb.interaction.guild_id:
            await interaction.response.send_message(
                "You must be in the same server as your battle to use commands.",
                ephemeral=True,
            )
            return

        if (interaction.user == gb.author and gb.author_ready) or (
            interaction.user == gb.opponent and gb.opponent_ready
        ):
            await interaction.response.send_message(
                f"You cannot change your {settings.plural_collectible_name} "
                f"as you are already ready.",
                ephemeral=True,
            )
            return

        user_balls = (
            gb.battle.p1_balls if interaction.user == gb.author else gb.battle.p2_balls
        )

        for countryball in countryballs:
            if hasattr(countryball, "ball") and countryball.ball:
                ball_name = countryball.ball.country
                emoji_id = countryball.ball.emoji_id
            else:
                ball_instance = await BallInstance.objects.prefetch_related(
                    "ball", "special"
                ).aget(id=countryball.id)
                ball_name = ball_instance.ball.country
                emoji_id = ball_instance.ball.emoji_id

            if not gb.allow_duplicates and any(b.name == ball_name for b in user_balls):
                yield True
                continue

            health = min(countryball.health, 5000)
            attack = min(countryball.attack, 5000)

            if (
                gb.allow_buffs
                and hasattr(countryball, "special")
                and countryball.special
                and "✨" in str(countryball.special)
            ):
                health += 2000
                attack += 2000

            ball = BattleBall(
                ball_name,
                interaction.user.name,
                health,
                attack,
                countryball.health_bonus,
                countryball.attack_bonus,
                self.bot.get_emoji(emoji_id) or "",
            )

            if ball in user_balls:
                yield True
                continue

            user_balls.append(ball)
            yield False

        if gb.battle_message and isinstance(gb.current_view, BattleSetupView):
            new_view = BattleSetupView(gb, announce_mention=gb.announce_mention)
            gb.current_view = new_view
            await gb.battle_message.edit(view=new_view)

    async def remove_balls(
        self,
        interaction: discord.Interaction,
        countryballs: list[BallInstance],
    ) -> AsyncGenerator[bool, None]:
        gb = fetch_battle(interaction.user)

        if gb is None:
            await interaction.response.send_message(
                "You aren't a part of a battle!", ephemeral=True
            )
            return

        if interaction.guild_id != gb.interaction.guild_id:
            await interaction.response.send_message(
                "You must be in the same server as your battle to use commands.",
                ephemeral=True,
            )
            return

        if (interaction.user == gb.author and gb.author_ready) or (
            interaction.user == gb.opponent and gb.opponent_ready
        ):
            await interaction.response.send_message(
                "You cannot change your balls as you are already ready.", ephemeral=True
            )
            return

        user_balls = (
            gb.battle.p1_balls if interaction.user == gb.author else gb.battle.p2_balls
        )

        for countryball in countryballs:
            if hasattr(countryball, "ball") and countryball.ball:
                ball_name = countryball.ball.country
                emoji_id = countryball.ball.emoji_id
            else:
                ball_instance = await BallInstance.objects.prefetch_related(
                    "ball", "special"
                ).aget(id=countryball.id)
                ball_name = ball_instance.ball.country
                emoji_id = ball_instance.ball.emoji_id

            health = min(countryball.health, 5000)
            attack = min(countryball.attack, 5000)

            if (
                gb.allow_buffs
                and hasattr(countryball, "special")
                and countryball.special
                and "✨" in str(countryball.special)
            ):
                health += 2000
                attack += 2000

            ball = BattleBall(
                ball_name,
                interaction.user.name,
                health,
                attack,
                countryball.health_bonus,
                countryball.attack_bonus,
                self.bot.get_emoji(emoji_id) or "",
            )

            if ball not in user_balls:
                yield True
                continue

            user_balls.remove(ball)
            yield False

        if gb.battle_message and isinstance(gb.current_view, BattleSetupView):
            new_view = BattleSetupView(gb, announce_mention=gb.announce_mention)
            gb.current_view = new_view
            await gb.battle_message.edit(view=new_view)

    @app_commands.command()
    async def add(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ) -> None:
        """
        Adds a countryball to the battle plan.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to add to your proposal
        """
        countryball = await BallInstance.objects.prefetch_related(
            "ball", "special"
        ).aget(id=countryball.id)

        async for dupe in self.add_balls(interaction, [countryball]):
            if dupe:
                await interaction.response.send_message(
                    "You cannot add the same ball twice!", ephemeral=True
                )
                return

        attack_fmt = "{:+}".format(countryball.attack_bonus)
        health_fmt = "{:+}".format(countryball.health_bonus)
        await interaction.response.send_message(
            f"Added `#{countryball.id} {countryball.ball.country} "
            f"({attack_fmt}%/{health_fmt}%)`!",
            ephemeral=True,
        )

    @app_commands.command()
    async def remove(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
        special: SpecialEnabledTransform | None = None,
    ) -> None:
        """
        Remove a countryball from what you proposed in the ongoing battle plan.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to remove from your proposal
        """
        countryball = await BallInstance.objects.prefetch_related(
            "ball", "special"
        ).aget(id=countryball.id)

        async for not_in_battle in self.remove_balls(interaction, [countryball]):
            if not_in_battle:
                await interaction.response.send_message(
                    f"You cannot remove a {settings.collectible_name} "
                    f"that is not in your deck!",
                    ephemeral=True,
                )
                return

        attack_fmt = "{:+}".format(countryball.attack_bonus)
        health_fmt = "{:+}".format(countryball.health_bonus)
        await interaction.response.send_message(
            f"Removed `#{countryball.id} {countryball.ball.country} "
            f"({attack_fmt}%/{health_fmt}%)`!",
            ephemeral=True,
        )
