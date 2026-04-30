import logging
import random
import sys
import time
from typing import TYPE_CHECKING, Dict
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

import asyncio
import io

from bd_models.models import (
    Ball,
    BallInstance,
    Player
)
from bd_models.models import balls as countryballs
from settings.models import settings

from ballsdex.core.utils.transformers import (
    BallEnabledTransform,
    BallInstanceTransform,
    SpecialEnabledTransform,
)

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
    emoji: str = ""
    dead: bool = False


@dataclass
class BattleInstance:
    p1_balls: list = field(default_factory=list)
    p2_balls: list = field(default_factory=list)
    winner: str = ""
    turns: int = 0


def get_damage(ball):
    return int(ball.attack * random.uniform(0.8, 1.2))


def attack(current_ball, enemy_balls):
    alive_balls = [ball for ball in enemy_balls if not ball.dead]
    enemy = random.choice(alive_balls)

    attack_dealt = get_damage(current_ball)
    enemy.health -= attack_dealt

    if enemy.health <= 0:
        enemy.health = 0
        enemy.dead = True
    if enemy.dead:
        gen_text = f"{current_ball.owner}'s {current_ball.name} has killed {enemy.owner}'s {enemy.name}"
    else:
        gen_text = f"{current_ball.owner}'s {current_ball.name} has dealt {attack_dealt} damage to {enemy.owner}'s {enemy.name}"
    return gen_text


def random_events():
    if random.randint(0, 100) <= 30:
        return 1
    else:
        return 0


def gen_battle(battle: BattleInstance):
    turn = 0
    
    # Get player names from first balls
    p1_name = battle.p1_balls[0].owner if battle.p1_balls else "Player1"
    p2_name = battle.p2_balls[0].owner if battle.p2_balls else "Player2"
    
    yield f"Battle between {p1_name} and {p2_name} begins! - {p1_name} begins"

    if all(
        ball.attack <= 0 for ball in battle.p1_balls + battle.p2_balls
    ):
        yield (
            "Everyone stared at each other, "
            "resulting in nobody winning."
        )
        return

    while any(ball for ball in battle.p1_balls if not ball.dead) and any(
        ball for ball in battle.p2_balls if not ball.dead
    ):
        alive_p1_balls = [ball for ball in battle.p1_balls if not ball.dead]
        alive_p2_balls = [ball for ball in battle.p2_balls if not ball.dead]

        for p1_ball, p2_ball in zip(alive_p1_balls, alive_p2_balls):

            if not p1_ball.dead:
                turn += 1

                event = random_events()
                if event == 1:
                    yield f"Turn {turn}: {p1_ball.owner}'s {p1_ball.name} missed {p2_ball.owner}'s {p2_ball.name}"
                    continue
                yield f"Turn {turn}: {attack(p1_ball, battle.p2_balls)}"

                if all(ball.dead for ball in battle.p2_balls):
                    break
            
            if not p2_ball.dead:
                turn += 1

                event = random_events()
                if event == 1:
                    yield f"Turn {turn}: {p2_ball.owner}'s {p2_ball.name} missed {p1_ball.owner}'s {p1_ball.name}"
                    continue
                yield f"Turn {turn}: {attack(p2_ball, battle.p1_balls)}"

                if all(ball.dead for ball in battle.p1_balls):
                    break

    if all(ball.dead for ball in battle.p1_balls):
        battle.winner = battle.p2_balls[0].owner
    elif all(ball.dead for ball in battle.p2_balls):
        battle.winner = battle.p1_balls[0].owner
    else:
        # Both have survivors - determine winner by HP remaining
        p1_total_hp = sum(ball.health for ball in battle.p1_balls if not ball.dead)
        p2_total_hp = sum(ball.health for ball in battle.p2_balls if not ball.dead)
        
        if p1_total_hp > p2_total_hp:
            battle.winner = battle.p1_balls[0].owner
        elif p2_total_hp > p1_total_hp:
            battle.winner = battle.p2_balls[0].owner
        else:
            battle.winner = "Draw"

    battle.turns = turn



battles = []

@dataclass
class GuildBattle:
    interaction: discord.Interaction

    author: discord.Member
    opponent: discord.Member

    battle: BattleInstance = field(default_factory=BattleInstance)
    author_ready: bool = False
    opponent_ready: bool = False
    battle_message: discord.Message = None
    amount_required: int = 3
    allow_duplicates: bool = True
    allow_buffs: bool = True
    created_at: float = field(default_factory=lambda: __import__('time').time())


def gen_deck(balls, strikethrough=False) -> str:
    """Generates a text representation of a player's deck."""
    if not balls:
        return "*Empty*"
    
    if strikethrough:
        deck = "\n".join(
            [
                f"-~~ {ball.emoji} {ball.name} ATK:{ball.attack_bonus:+}% HP:{ball.health_bonus:+}%~~"
                for ball in balls
            ]
        )
    else:
        deck = "\n".join(
            [
                f"- {ball.emoji} {ball.name} ATK:{ball.attack_bonus:+}% HP:{ball.health_bonus:+}%"
                for ball in balls
            ]
        )
    
    if len(deck) > 1024:
        return deck[0:951] + '\n<truncated due to discord limits, rest of your balls are still here>'
    return deck

def update_embed(
    author_balls, opponent_balls, author, opponent, author_ready, opponent_ready, allow_duplicates, allow_buffs, amount_required
) -> discord.Embed:
    """Creates an embed for the battle setup phase."""
    embed = discord.Embed(
        title=f"{settings.plural_collectible_name.title()} Battle Plan",
        description=(
            f"Add or remove {settings.plural_collectible_name} you want to propose to the other player using the "
            "`/battle add` and `/battle remove` commands.\n"
            "Once you're finished, click the lock button to confirm your proposal.\n"
            f"*You have 15 minutes before this interaction ends.*\n"
            f"**Settings**:\n"
            f"• Duplicates: {'Allowed' if allow_duplicates else 'Not allowed'}\n"
            f"• Buffs: {'Allowed' if allow_buffs else 'Not allowed'}\n"
            f"• Amount: {amount_required}"
        ),
        color=discord.Color.blurple(),
    )

    author_emoji = "🔒" if author_ready else ""
    opponent_emoji = "🔒" if opponent_ready else ""

    embed.add_field(
        name=f"{author_emoji} {author}",
        value=gen_deck(author_balls),
        inline=True,
    )
    embed.add_field(
        name=f"{opponent_emoji} {opponent}",
        value=gen_deck(opponent_balls),
        inline=True,
    )
    return embed


class ReadyView(discord.ui.View):
    def __init__(self, guild_battle):
        super().__init__(timeout=None)
        self.guild_battle = guild_battle

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self.guild_battle.author, self.guild_battle.opponent):
            await interaction.response.send_message("You cannot interact with this battle!", ephemeral=True)
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.success, emoji="✔️", label="")
    async def execute_battle(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Execute battle logic
        battle_log = "\n".join(gen_battle(self.guild_battle.battle))

        # First message: Battle plan concluded
        battle_plan_embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Battle Plan",
            description="Battle Plan concluded!",
            color=discord.Color(0x2ecc70),
        )
        battle_plan_embed.add_field(
            name=f"✅ {self.guild_battle.author.name}",
            value=gen_deck(self.guild_battle.battle.p1_balls),
            inline=True,
        )
        battle_plan_embed.add_field(
            name=f"✅ {self.guild_battle.opponent.name}",
            value=gen_deck(self.guild_battle.battle.p2_balls),
            inline=True,
        )
        battle_plan_embed.set_footer(text="This message is updated every 15 seconds, but you can keep on editing your battle proposal.")

        # Second message: Battle logs
        battle_logs_embed = discord.Embed(
            title=f"Battle between {self.guild_battle.author.name} and {self.guild_battle.opponent.name}",
            description=(
                f"Battle settings:\n\n"
                f"Duplicates: {'Allowed' if self.guild_battle.allow_duplicates else 'Not allowed'}\n"
                f"Buffs: {'Allowed' if self.guild_battle.allow_buffs else 'Not allowed'}\n"
                f"Amount: {self.guild_battle.amount_required}"
            ),
            color=discord.Color.blurple(),
        )
        battle_logs_embed.add_field(
            name=f"**{self.guild_battle.author.name}'s deck:**",
            value=gen_deck(self.guild_battle.battle.p1_balls),
            inline=True,
        )
        battle_logs_embed.add_field(
            name=f"**{self.guild_battle.opponent.name}'s deck:**",
            value=gen_deck(self.guild_battle.battle.p2_balls),
            inline=True,
        )
        battle_logs_embed.add_field(
            name="**Winner:**",
            value=f"{self.guild_battle.battle.winner} - Turn: {self.guild_battle.battle.turns}",
            inline=False,
        )
        battle_logs_embed.set_footer(text="Battle log is attached.")
        
        await interaction.response.defer()
        
        # Create disabled buttons view for battle plan concluded
        concluded_view = ReadyView(self.guild_battle)
        for item in concluded_view.children:
            item.disabled = True
        
        # Update original message to battle plan concluded
        await interaction.message.edit(
            content=f"{self.guild_battle.author.mention} vs {self.guild_battle.opponent.mention}",
            embed=battle_plan_embed,
            view=concluded_view,  # Disabled buttons
        )
        
        # Send battle logs as separate message
        await interaction.channel.send(
            embed=battle_logs_embed,
            file=discord.File(io.StringIO(battle_log), filename="battle-log.txt")
        )

        battles.pop(battles.index(self.guild_battle))
        
        # Stop the message update task by setting battle_message to None
        self.guild_battle.battle_message = None

    @discord.ui.button(style=discord.ButtonStyle.danger, emoji="✖️", label="")
    async def cancel_battle(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Cancel battle logic (same as cancel_button)
        # Determine who cancelled and show red stop circle next to their name
        if interaction.user == self.guild_battle.author:
            author_name = f"🚫 {self.guild_battle.author.name}"
            opponent_name = self.guild_battle.opponent.name
        else:
            author_name = self.guild_battle.author.name
            opponent_name = f"🚫 {self.guild_battle.opponent.name}"

        embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Battle Plan",
            description="**The battle has been cancelled.**",
            color=discord.Color(0xe74d3c),
        )
        embed.add_field(
            name=author_name,
            value=gen_deck(self.guild_battle.battle.p1_balls, strikethrough=bool(self.guild_battle.battle.p1_balls)),
            inline=True,
        )
        embed.add_field(
            name=opponent_name,
            value=gen_deck(self.guild_battle.battle.p2_balls, strikethrough=bool(self.guild_battle.battle.p2_balls)),
            inline=True,
        )
        embed.set_footer(text="This message is updated every 15 seconds, but you can keep on editing your battle proposal.")

        # Update the public battle message to remove invitation text
        if self.guild_battle.battle_message:
            # Use BattleSetupView buttons but disabled for cancel state
            cancel_view = BattleSetupView(self.guild_battle.interaction, self.guild_battle.author, self.guild_battle.opponent)
            for item in cancel_view.children:
                item.disabled = True
            await self.guild_battle.battle_message.edit(
                content="",  # Remove invitation text
                embed=embed,
                view=cancel_view
            )

        try:
            await interaction.response.send_message(
                "Battle has been cancelled.",
                ephemeral=True
            )
        except discord.errors.InteractionResponded:
            pass
            
        # Remove from battles list
        battles.pop(battles.index(self.guild_battle))
        
        # Stop the message update task by setting battle_message to None
        self.guild_battle.battle_message = None


def fetch_battle(user: discord.User | discord.Member):
    """
    Fetches a battle based on the user provided.

    Parameters
    ----------
    user: discord.User | discord.Member
        The user you want to fetch the battle from.
    """
    found_battle = None

    for battle in battles:
        if user not in (battle.author, battle.opponent):
            continue

        found_battle = battle
        break

    return found_battle


class BattleSetupView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, author: discord.Member, opponent: discord.Member):
        super().__init__(timeout=None)
        self.interaction = interaction
        self.author = author
        self.opponent = opponent

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self.author, self.opponent):
            await interaction.response.send_message("You cannot interact with this battle!", ephemeral=True)
            return False
        return True

    @discord.ui.button(style=discord.ButtonStyle.primary, emoji="🔒", label="Lock proposal")
    async def ready_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_battle = None
        for battle in battles:
            if battle.interaction == self.interaction:
                guild_battle = battle
                break
        
        if guild_battle is None:
            await interaction.response.send_message("Battle not found!", ephemeral=True)
            return

        user_balls = (
            guild_battle.battle.p1_balls
            if interaction.user == guild_battle.author
            else guild_battle.battle.p2_balls
        )
        
        if len(user_balls) < guild_battle.amount_required:
            await interaction.response.send_message(
                f"You need to have exactly {guild_battle.amount_required} {settings.plural_collectible_name} in your proposal to lock it.", ephemeral=True
            )
            return

        if interaction.user == guild_battle.author:
            guild_battle.author_ready = True
        elif interaction.user == guild_battle.opponent:
            guild_battle.opponent_ready = True

        if guild_battle.author_ready and guild_battle.opponent_ready:
            if len(guild_battle.battle.p1_balls) < guild_battle.amount_required or len(guild_battle.battle.p2_balls) < guild_battle.amount_required:
                await interaction.response.send_message(
                    f"Both users must add at least {guild_battle.amount_required} {settings.plural_collectible_name}!", ephemeral=True
                )
                return
            
            new_view = ReadyView(guild_battle)
            
            embed = discord.Embed(
                title=f"{settings.plural_collectible_name.title()} Battle Plan",
                description="Both users have locked their propositions! Now confirm to begin the battle.",
                color=discord.Color(0xfee65c),
            )
            embed.add_field(
                name=f"🔒 {guild_battle.author.name}",
                value=gen_deck(guild_battle.battle.p1_balls),
                inline=True,
            )
            embed.add_field(
                name=f"🔒 {guild_battle.opponent.name}",
                value=gen_deck(guild_battle.battle.p2_balls),
                inline=True,
            )
            embed.set_footer(text="This message is updated every 15 seconds, but you can keep on editing your battle proposal.")
            
            await interaction.response.defer()
            await interaction.message.edit(
                content=f"{guild_battle.author.mention} vs {guild_battle.opponent.mention}",
                embed=embed,
                view=new_view,
            )
        else:
            await interaction.response.send_message(
                f"Done! Waiting for the other player to press 'Ready'.", ephemeral=True
            )

            author_emoji = (
                "🔒" if interaction.user == guild_battle.author else ""
            )
            opponent_emoji = (
                "🔒" if interaction.user == guild_battle.opponent else ""
            )

            embed = discord.Embed(
                title=f"{settings.plural_collectible_name.title()} Battle Plan",
                description=(
                    f"Add or remove {settings.plural_collectible_name} you want to propose to the other player using the "
                    "'/battle add' and '/battle remove' commands.\n"
                    "Once you're finished, click the lock button to confirm your proposal."
                ),
                color=discord.Color.blurple(),
            )

            # Use lock emojis for ready players
            author_emoji = "🔒" if guild_battle.author_ready else ""
            opponent_emoji = "🔒" if guild_battle.opponent_ready else ""

            embed.add_field(
                name=f"{author_emoji} {guild_battle.author.name}'s deck:",
                value=gen_deck(guild_battle.battle.p1_balls),
                inline=True,
            )
            embed.add_field(
                name=f"{opponent_emoji} {guild_battle.opponent.name}'s deck:",
                value=gen_deck(guild_battle.battle.p2_balls),
                inline=True,
            )

            # Update the public battle message
            if guild_battle.battle_message:
                await guild_battle.battle_message.edit(embed=embed)

    @discord.ui.button(style=discord.ButtonStyle.secondary, emoji="💨", label="Reset")
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_battle = None
        for battle in battles:
            if battle.interaction == self.interaction:
                guild_battle = battle
                break
        
        if guild_battle is None:
            await interaction.response.send_message("Battle not found!", ephemeral=True)
            return

        # Clear the player's balls
        user_balls = (
            guild_battle.battle.p1_balls
            if interaction.user == guild_battle.author
            else guild_battle.battle.p2_balls
        )
        user_balls.clear()

        # Update the public battle message
        if guild_battle.battle_message:
            embed = update_embed(
                guild_battle.battle.p1_balls,
                guild_battle.battle.p2_balls,
                guild_battle.author.name,
                guild_battle.opponent.name,
                guild_battle.author_ready,
                guild_battle.opponent_ready,
                guild_battle.allow_duplicates,
                guild_battle.allow_buffs,
                guild_battle.amount_required,
            )
            await guild_battle.battle_message.edit(embed=embed)

        await interaction.response.send_message(
            "Your countryballs have been reset!",
            ephemeral=True
        )

    @discord.ui.button(style=discord.ButtonStyle.danger, emoji="✖️", label="Cancel battle")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_battle = None
        for battle in battles:
            if battle.interaction == self.interaction:
                guild_battle = battle
                break
        
        if guild_battle is None:
            await interaction.response.send_message("Battle not found!", ephemeral=True)
            return

        # Determine who cancelled and show red stop circle next to their name
        if interaction.user == guild_battle.author:
            author_name = f"🚫 {guild_battle.author.name}"
            opponent_name = guild_battle.opponent.name
        else:
            author_name = guild_battle.author.name
            opponent_name = f"🚫 {guild_battle.opponent.name}"

        embed = discord.Embed(
            title=f"{settings.plural_collectible_name.title()} Battle Plan",
            description="**The battle has been cancelled.**",
            color=discord.Color(0xe74d3c),
        )
        embed.add_field(
            name=author_name,
            value=gen_deck(guild_battle.battle.p1_balls),
            inline=True,
        )
        embed.add_field(
            name=opponent_name,
            value=gen_deck(guild_battle.battle.p2_balls),
            inline=True,
        )
        embed.set_footer(text="This message is updated every 15 seconds, but you can keep on editing your battle proposal.")

        # Update the public battle message to remove invitation text
        if guild_battle.battle_message:
            # Use BattleSetupView buttons but disabled for cancel state
            cancel_view = BattleSetupView(guild_battle.interaction, guild_battle.author, guild_battle.opponent)
            for item in cancel_view.children:
                item.disabled = True
            await guild_battle.battle_message.edit(
                content="",  # Remove invitation text
                embed=embed,
                view=cancel_view
            )

        try:
            await interaction.response.send_message(
                "Battle has been cancelled.",
                ephemeral=True
            )
        except discord.errors.InteractionResponded:
            pass
            
        battles.pop(battles.index(guild_battle))
        
        # Stop the message update task by setting battle_message to None
        guild_battle.battle_message = None


class Battle(commands.GroupCog):
    """
    Battle your countryballs!
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        # Start battle expiration checker
        asyncio.create_task(self._battle_expiration_checker())


    async def _battle_expiration_checker(self):
        """Checks for expired battles every minute."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                current_time = time.time()
                expired_battles = []
                
                for battle in battles[:]:  # Copy list to avoid modification during iteration
                    if current_time - battle.created_at > 900:  # 15 minutes = 900 seconds
                        expired_battles.append(battle)
                
                # Handle expired battles
                for battle in expired_battles:
                    try:
                        if battle.battle_message:
                            embed = discord.Embed(
                                title=f"{settings.plural_collectible_name.title()} Battle Plan",
                                description="The battle timed out",
                                color=discord.Color(0x992e22),
                            )
                            embed.add_field(
                                name=f"{battle.author.name}'s deck:",
                                value=gen_deck(battle.p1_balls),
                                inline=True,
                            )
                            embed.add_field(
                                name=f"{battle.opponent.name}'s deck:",
                                value=gen_deck(battle.p2_balls),
                                inline=True,
                            )
                            embed.set_footer(text="This message is updated every 15 seconds, but you can keep on editing your battle proposal.")
                            

                            # Determine which buttons to show based on battle state, but disabled
                            if battle.author_ready and battle.opponent_ready:
                                # Both locked - use ReadyView (disabled)
                                timeout_view = ReadyView(battle)
                                # Disable all buttons in the view
                                for item in timeout_view.children:
                                    item.disabled = True
                            else:
                                # Not both locked - use BattleSetupView with disabled buttons
                                timeout_view = BattleSetupView(battle.interaction, battle.author, battle.opponent)
                                # Disable all buttons in the view
                                for item in timeout_view.children:
                                    item.disabled = True
                            

                            await battle.battle_message.edit(
                                embed=embed,
                                view=timeout_view
                            )
                        
                        # Remove from battles list and stop message update task
                        if battle in battles:
                            battles.remove(battle)
                        # Stop the message update task by setting battle_message to None
                        battle.battle_message = None
                            
                    except Exception as e:
                        log.error(f"Error handling expired battle: {e}")
                        # Ensure cleanup even if message edit fails
                        if battle in battles:
                            battles.remove(battle)
                        battle.battle_message = None
                        
            except Exception as e:
                log.error(f"Battle expiration checker error: {e}")
                await asyncio.sleep(60)  # Wait before retrying

    
    @app_commands.command()
    async def start(self, interaction: discord.Interaction, user: discord.Member, duplicates: bool = True, buffs: bool = True, amount: int = 3):
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
                "You can't battle against bots.", ephemeral=True,
            )
            return
        
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "You can't battle against yourself.", ephemeral=True,
            )
            return

        if fetch_battle(user) is not None:
            await interaction.response.send_message(
                "That user is already in a battle.", ephemeral=True,
            )
            return

        if fetch_battle(interaction.user) is not None:
            await interaction.response.send_message(
                "You are already in a battle.", ephemeral=True,
            )
            return

        # Validate amount
        if amount < 3 or amount > 10:
            await interaction.response.send_message(
                "Amount must be between 3 and 10 countryballs!", ephemeral=True,
            )
            return
        
        battles.append(GuildBattle(interaction, interaction.user, user, amount_required=amount, allow_duplicates=duplicates, allow_buffs=buffs))

        embed = update_embed([], [], interaction.user.name, user.name, False, False, duplicates, buffs, amount)

        view = BattleSetupView(interaction, interaction.user, user)

        # Send ephemeral confirmation to initiator
        await interaction.response.send_message(
            "battle started!",
            ephemeral=True,
        )

        # Send public battle message
        battle_message = await interaction.channel.send(
            f"Hey, {user.mention}, {interaction.user.name} is proposing a battle with you!",
            embed=embed,
            view=view,
        )

        # Update the guild battle with the public message
        guild_battle = fetch_battle(interaction.user)
        guild_battle.battle_message = battle_message
        
        # Start the message update task
        asyncio.create_task(self._update_battle_message(guild_battle))

    async def _update_battle_message(self, guild_battle):
        """Updates the battle message every 15 seconds to keep it alive."""
        while guild_battle in battles and guild_battle.battle_message:
            try:
                await asyncio.sleep(15)
                if guild_battle in battles and guild_battle.battle_message:
                    # Just edit with the same content to keep it alive
                    embed = update_embed(
                        guild_battle.battle.p1_balls,
                        guild_battle.battle.p2_balls,
                        guild_battle.author.name,
                        guild_battle.opponent.name,
                        guild_battle.author_ready,
                        guild_battle.opponent_ready,
                        guild_battle.allow_duplicates,
                        guild_battle.allow_buffs,
                        guild_battle.amount_required,
                    )
                    await guild_battle.battle_message.edit(embed=embed)
            except (discord.NotFound, discord.Forbidden):
                # Message was deleted or we can't edit it, stop updating
                break
            except Exception as e:
                log.error(f"Error updating battle message: {e}")
                break

    async def add_balls(self, interaction: discord.Interaction, countryballs):
        guild_battle = fetch_battle(interaction.user)

        if guild_battle is None:
            await interaction.response.send_message(
                "You aren't a part of a battle!", ephemeral=True
            )
            return
        
        if interaction.guild_id != guild_battle.interaction.guild_id:
            await interaction.response.send_message(
                "You must be in the same server as your battle to use commands.", ephemeral=True
            )
            return

        if (interaction.user == guild_battle.author and guild_battle.author_ready) or (
            interaction.user == guild_battle.opponent and guild_battle.opponent_ready
        ):
            await interaction.response.send_message(
                f"You cannot change your {settings.plural_collectible_name} as you are already ready.", ephemeral=True
            )
            return

        user_balls = (
            guild_battle.battle.p1_balls
            if interaction.user == guild_battle.author
            else guild_battle.battle.p2_balls
        )
        if hasattr(countryballs, '__aiter__'):
            async for countryball in countryballs:
                if hasattr(countryball, 'ball') and countryball.ball:
                    ball_name = countryball.ball.country
                    emoji_id = countryball.ball.emoji_id
                else:
                    ball_instance = await BallInstance.objects.prefetch_related("ball", "special").aget(id=countryball.id)
                    ball_name = ball_instance.ball.country
                    emoji_id = ball_instance.ball.emoji_id
                
                # Check for duplicates if not allowed
                if not guild_battle.allow_duplicates:
                    if any(b.name == ball_name for b in user_balls):
                        yield True  # Duplicate found
                        continue
                
                # Apply stat caps before buffs
                health = min(countryball.health, 5000)
                attack = min(countryball.attack, 5000)
                
                if guild_battle.allow_buffs and hasattr(countryball, 'special') and countryball.special and "✨" in str(countryball.special):
                    health += 2000
                    attack += 2000
                
                ball = BattleBall(
                    ball_name,
                    interaction.user.name,
                    health,
                    attack,
                    countryball.health_bonus,
                    countryball.attack_bonus,
                    self.bot.get_emoji(emoji_id),
                )

                if ball in user_balls:
                    yield True
                    continue
                
                user_balls.append(ball)
                yield False
        else:
            for countryball in countryballs:
                if hasattr(countryball, 'ball') and countryball.ball:
                    ball_name = countryball.ball.country
                    emoji_id = countryball.ball.emoji_id
                else:
                    ball_instance = await BallInstance.objects.prefetch_related("ball", "special").aget(id=countryball.id)
                    ball_name = ball_instance.ball.country
                    emoji_id = ball_instance.ball.emoji_id
                
                # Check for duplicates if not allowed
                if not guild_battle.allow_duplicates:
                    if any(b.name == ball_name for b in user_balls):
                        yield True  # Duplicate found
                        continue
                
                # Apply stat caps before buffs
                health = min(countryball.health, 5000)
                attack = min(countryball.attack, 5000)
                
                if guild_battle.allow_buffs and hasattr(countryball, 'special') and countryball.special and "✨" in str(countryball.special):
                    health += 2000
                    attack += 2000
                
                ball = BattleBall(
                    ball_name,
                    interaction.user.name,
                    health,
                    attack,
                    countryball.health_bonus,
                    countryball.attack_bonus,
                    self.bot.get_emoji(emoji_id),
                )

                if ball in user_balls:
                    yield True
                    continue
                
                user_balls.append(ball)
                yield False

        
        # Update the public battle message
        if guild_battle.battle_message:
            await guild_battle.battle_message.edit(
                embed=update_embed(
                    guild_battle.battle.p1_balls,
                    guild_battle.battle.p2_balls,
                    guild_battle.author.name,
                    guild_battle.opponent.name,
                    guild_battle.author_ready,
                    guild_battle.opponent_ready,
                    guild_battle.allow_duplicates,
                    guild_battle.allow_buffs,
                    guild_battle.amount_required,
                )
            )

    async def remove_balls(self, interaction: discord.Interaction, countryballs):
        guild_battle = fetch_battle(interaction.user)

        if guild_battle is None:
            await interaction.response.send_message(
                "You aren't a part of a battle!", ephemeral=True
            )
            return
        
        if interaction.guild_id != guild_battle.interaction.guild_id:
            await interaction.response.send_message(
                "You must be in the same server as your battle to use commands.", ephemeral=True
            )
            return

        if (interaction.user == guild_battle.author and guild_battle.author_ready) or (
            interaction.user == guild_battle.opponent and guild_battle.opponent_ready
        ):
            await interaction.response.send_message(
                "You cannot change your balls as you are already ready.", ephemeral=True
            )
            return

        user_balls = (
            guild_battle.battle.p1_balls
            if interaction.user == guild_battle.author
            else guild_battle.battle.p2_balls
        )
        if hasattr(countryballs, '__aiter__'):
            async for countryball in countryballs:
                if hasattr(countryball, 'ball') and countryball.ball:
                    ball_name = countryball.ball.country
                    emoji_id = countryball.ball.emoji_id
                else:
                    ball_instance = await BallInstance.objects.prefetch_related("ball", "special").aget(id=countryball.id)
                    ball_name = ball_instance.ball.country
                    emoji_id = ball_instance.ball.emoji_id
                
                health = min(countryball.health, 5000)
                attack = min(countryball.attack, 5000)
                
                if guild_battle.allow_buffs and hasattr(countryball, 'special') and countryball.special and "✨" in str(countryball.special):
                    health += 2000
                    attack += 2000

                ball = BattleBall(
                    ball_name,
                    interaction.user.name,
                    health,
                    attack,
                    countryball.health_bonus,
                    countryball.attack_bonus,
                    self.bot.get_emoji(emoji_id),
                )

                if ball not in user_balls:
                    yield True
                    continue
                
                user_balls.remove(ball)
                yield False
        else:
            for countryball in countryballs:
                if hasattr(countryball, 'ball') and countryball.ball:
                    ball_name = countryball.ball.country
                    emoji_id = countryball.ball.emoji_id
                else:
                    ball_instance = await BallInstance.objects.prefetch_related("ball", "special").aget(id=countryball.id)
                    ball_name = ball_instance.ball.country
                    emoji_id = ball_instance.ball.emoji_id
                
                health = min(countryball.health, 5000)
                attack = min(countryball.attack, 5000)
                
                if guild_battle.allow_buffs and hasattr(countryball, 'special') and countryball.special and "✨" in str(countryball.special):
                    health += 2000
                    attack += 2000

                ball = BattleBall(
                    ball_name,
                    interaction.user.name,
                    health,
                    attack,
                    countryball.health_bonus,
                    countryball.attack_bonus,
                    self.bot.get_emoji(emoji_id),
                )

                if ball not in user_balls:
                    yield True
                    continue
                
                user_balls.remove(ball)
                yield False

        # Update the public battle message
        if guild_battle.battle_message:
            await guild_battle.battle_message.edit(
                embed=update_embed(
                    guild_battle.battle.p1_balls,
                    guild_battle.battle.p2_balls,
                    guild_battle.author.name,
                    guild_battle.opponent.name,
                    guild_battle.author_ready,
                    guild_battle.opponent_ready,
                    guild_battle.allow_duplicates,
                    guild_battle.allow_buffs,
                    guild_battle.amount_required,
                )
            )

    @app_commands.command()
    async def add(
        self, interaction: discord.Interaction, countryball: BallInstanceTransform, special: SpecialEnabledTransform | None = None
    ):
        """
        Adds a countryball to the battle plan.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to add to your proposal
        """
        countryball = await BallInstance.objects.prefetch_related("ball", "special").aget(id=countryball.id)

        async for dupe in self.add_balls(interaction, [countryball]):
            if dupe:
                await interaction.response.send_message(
                    "You cannot add the same ball twice!", ephemeral=True
                )
                return

        attack = "{:+}".format(countryball.attack_bonus)
        health = "{:+}".format(countryball.health_bonus)

        await interaction.response.send_message(
            f"Added `#{countryball.id} {countryball.ball.country} ({attack}%/{health}%)`!",
            ephemeral=True,
        )

    @app_commands.command()
    async def remove(
        self, interaction: discord.Interaction, countryball: BallInstanceTransform, special: SpecialEnabledTransform | None = None
    ):
        """
        Remove a countryball from what you proposed in the ongoing battle plan.

        Parameters
        ----------
        countryball: Ball
            The countryball you want to remove from your proposal
        """
        countryball = await BallInstance.objects.prefetch_related("ball", "special").aget(id=countryball.id)

        async for not_in_battle in self.remove_balls(interaction, [countryball]):
            if not_in_battle:
                await interaction.response.send_message(
                    f"You cannot remove a {settings.collectible_name} that is not in your deck!", ephemeral=True
                )
                return

        attack = "{:+}".format(countryball.attack_bonus)
        health = "{:+}".format(countryball.health_bonus)

        await interaction.response.send_message(
            f"Removed `#{countryball.id} {countryball.ball.country} ({attack}%/{health}%)`!",
            ephemeral=True,
        )
