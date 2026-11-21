// Copyright Epic Games, Inc. All Rights Reserved.

using UnrealBuildTool;

public class WishCraft : ModuleRules
{
	public WishCraft(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[] {
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			"EnhancedInput",
			"AIModule",
			"StateTreeModule",
			"GameplayStateTreeModule",
			"UMG",
			"Slate",
			// Attach-to-socket ability effects are spawned from C++, so the runtime module is needed here.
			"Niagara"
		});

		PrivateDependencyModuleNames.AddRange(new string[] { "HTTP", "Json",
			// The character casts to ASharedCameraManager to swap the view target, .cpp only.
			"SharedCameraSystem" });

		PublicIncludePaths.AddRange(new string[] {
			"WishCraft",
			"WishCraft/Abilities",
			"WishCraft/Rules",
			"WishCraft/Variant_Combat",
			"WishCraft/Variant_Combat/AI",
			"WishCraft/Variant_Combat/Animation",
			"WishCraft/Variant_Combat/Gameplay",
			"WishCraft/Variant_Combat/Interfaces",
			"WishCraft/Variant_Combat/UI"
		});

		// Uncomment if you are using Slate UI
		// PrivateDependencyModuleNames.AddRange(new string[] { "Slate", "SlateCore" });

		// Uncomment if you are using online features
		// PrivateDependencyModuleNames.Add("OnlineSubsystem");

		// To include OnlineSubsystemSteam, add it to the plugins section in your uproject file with the Enabled attribute set to true
	}
}
