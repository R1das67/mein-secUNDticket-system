import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ================= STORAGE =================
panels = {
    "panel1": {"name": None, "embed": None, "mod_role": None, "category": None},
    "panel2": {"name": None, "embed": None, "mod_role": None, "category": None}
}
ticket_count = 0

# ================= ADMIN CHECK =================
def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator

# ================= BOT EVENTS =================
@bot.event
async def on_ready():
    print(f"✅ Bot online als {bot.user}")
    bot.add_view(TicketOpenButton())  # Persistent View für Ticket-Buttons
    bot.add_view(TicketCloseButton())
    await bot.tree.sync()
    print("Slash Commands synchronisiert")

# ================= INTERACTIVE PANEL SETUP =================
class PanelSetup:
    """Hilfsklasse, um Panel Setup zwischen Nachrichten zu speichern"""
    def __init__(self, panel_key, interaction):
        self.panel_key = panel_key
        self.interaction = interaction
        self.step = 0  # 0=Name,1=Embed,2=Mod,3=Kategorie
        self.temp_data = {}

    async def next_step(self):
        if self.step == 0:
            await self.interaction.response.send_message(
                "Bitte gib den Panel-Namen ein:\n`set-panel-name <Name>`", ephemeral=True
            )
        elif self.step == 1:
            await self.interaction.followup.send(
                "Bitte gib den Embed-Text ein:\n`set-embed-text <Text>`", ephemeral=True
            )
        elif self.step == 2:
            await self.interaction.followup.send(
                "Bitte gib die Mod-Rolle ein:\n`set-ticket-mod <Role ID>`", ephemeral=True
            )
        elif self.step == 3:
            await self.interaction.followup.send(
                "Bitte gib die Ticket-Kategorie ein:\n`create-tickets-in <Kategorie ID>`", ephemeral=True
            )
        elif self.step == 4:
            # Confirm/Go Back
            view = ConfirmFinishView(self)
            embed = discord.Embed(
                title="Panel Setup abgeschlossen",
                description=f"**Name:** {self.temp_data['name']}\n**Embed:** {self.temp_data['embed']}\n**Mod-Rolle:** <@&{self.temp_data['mod_role']}> \n**Kategorie:** <#{self.temp_data['category']}>",
                color=discord.Color.blue()
            )
            await self.interaction.followup.send("Finish?", embed=embed, view=view, ephemeral=True)

# ================= SLASH COMMANDS =================
@bot.tree.command(name="edit-panel1", description="Editiere Panel1")
@app_commands.check(is_admin)
async def edit_panel1(interaction: discord.Interaction):
    setup = PanelSetup("panel1", interaction)
    bot.current_setup = setup
    await setup.next_step()

@bot.tree.command(name="edit-panel2", description="Editiere Panel2")
@app_commands.check(is_admin)
async def edit_panel2(interaction: discord.Interaction):
    setup = PanelSetup("panel2", interaction)
    bot.current_setup = setup
    await setup.next_step()

# ================= MESSAGE LISTENER FÜR SET-BEFEHLE =================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Prüfen, ob ein Setup läuft
    setup = getattr(bot, "current_setup", None)
    if not setup:
        return

    content = message.content.strip()
    key = setup.panel_key

    try:
        if content.startswith("set-panel-name"):
            name = content[len("set-panel-name"):].strip()
            setup.temp_data['name'] = name
            setup.step += 1
            await setup.next_step()

        elif content.startswith("set-embed-text"):
            embed_text = content[len("set-embed-text"):].strip()
            setup.temp_data['embed'] = embed_text
            setup.step += 1
            await setup.next_step()

        elif content.startswith("set-ticket-mod"):
            role_id = int(content[len("set-ticket-mod"):].strip())
            setup.temp_data['mod_role'] = role_id
            setup.step += 1
            await setup.next_step()

        elif content.startswith("create-tickets-in"):
            category_id = int(content[len("create-tickets-in"):].strip())
            setup.temp_data['category'] = category_id
            setup.step += 1
            await setup.next_step()
    except Exception as e:
        await message.channel.send(f"Fehler: {e}", ephemeral=True)

# ================= CONFIRM / GO BACK BUTTONS =================
class ConfirmFinishView(discord.ui.View):
    def __init__(self, setup):
        super().__init__(timeout=None)
        self.setup = setup
        self.add_item(ConfirmYesButton(setup))
        self.add_item(ConfirmNoButton(setup))

class ConfirmYesButton(discord.ui.Button):
    def __init__(self, setup):
        super().__init__(label="Yes", style=discord.ButtonStyle.success)
        self.setup = setup

    async def callback(self, interaction: discord.Interaction):
        # Setup speichern
        panels[self.setup.panel_key] = self.setup.temp_data
        embed = discord.Embed(
            title=self.setup.temp_data['name'],
            description=self.setup.temp_data['embed'],
            color=discord.Color.green()
        )
        await interaction.channel.send(embed=embed, view=TicketOpenButton())
        await interaction.response.send_message("✅ Panel erstellt!", ephemeral=True)

class ConfirmNoButton(discord.ui.Button):
    def __init__(self, setup):
        super().__init__(label="Go Back", style=discord.ButtonStyle.secondary)
        self.setup = setup

    async def callback(self, interaction: discord.Interaction):
        self.setup.step = 0
        self.setup.temp_data = {}
        await interaction.response.send_message("Setup wird neu gestartet...", ephemeral=True)
        await self.setup.next_step()

# ================= TICKET BUTTONS =================
class TicketOpenButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📨 Ticket erstellen", style=discord.ButtonStyle.primary)
    async def ticket_open(self, interaction: discord.Interaction, button: discord.ui.Button):
        global ticket_count
        # Panel auswählen nach Channel oder default Panel1
        panel = panels.get("panel1")
        if not panel:
            await interaction.response.send_message("❌ Kein Panel konfiguriert!", ephemeral=True)
            return

        ticket_count += 1
        guild = interaction.guild
        category = guild.get_channel(panel['category'])
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        if panel['mod_role']:
            mod_role = guild.get_role(panel['mod_role'])
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await guild.create_text_channel(
            name=f"ticket-{ticket_count}",
            category=category,
            overwrites=overwrites
        )

        ping_text = f"<@{interaction.user.id}>"
        if panel['mod_role']:
            ping_text = f"<@&{panel['mod_role']}> {ping_text}"

        await channel.send(ping_text)
        embed = discord.Embed(
            description="Bitte haben Sie etwas Geduld, der Support wird sich um Sie kümmern.",
            color=discord.Color.green()
        )
        await channel.send(embed=embed, view=TicketCloseButton())
        await interaction.response.send_message(f"✅ Ticket erstellt: {channel.mention}", ephemeral=True)

class TicketCloseButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="❌ Ticket schließen", style=discord.ButtonStyle.danger)
    async def ticket_close(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ConfirmCloseView(interaction.channel)
        await interaction.response.send_message("Möchten Sie das Ticket wirklich schließen?", view=view, ephemeral=True)

class ConfirmCloseView(discord.ui.View):
    def __init__(self, channel):
        super().__init__(timeout=30)
        self.channel = channel
        self.add_item(ConfirmCloseYes(channel))
        self.add_item(ConfirmCloseNo())

class ConfirmCloseYes(discord.ui.Button):
    def __init__(self, channel):
        super().__init__(label="Ja", style=discord.ButtonStyle.success)
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Ticket wird geschlossen...", ephemeral=True)
        await asyncio.sleep(2)
        await self.channel.delete()

class ConfirmCloseNo(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Nein", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("❌ Ticket bleibt geöffnet.", ephemeral=True)

# ================= START BOT =================
bot.run(os.getenv("DISCORD_TOKEN"))
