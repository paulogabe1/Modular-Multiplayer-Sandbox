// Defense slot: hides the avatar for a duration.
#include "Abilities/Ability_Invisibility.h"

#include "WishCraftCharacter.h"

float UAbility_Invisibility::BeginEffect()
{
	Super::BeginEffect();
	return GetParam(TEXT("Duration"), 5.f);
}

void UAbility_Invisibility::HandleCosmetic(bool bStarting)
{
	Super::HandleCosmetic(bStarting);
	ApplyInvisibility(bStarting);
}

void UAbility_Invisibility::ApplyInvisibility(bool bInvisible)
{
	// The character owns the replicated flag, so the server drives it and every client
	// mirrors it through OnRep_Invisible. Routing the visual through replicated state
	// rather than a multicast keeps the avatar hidden for players whose machine is not
	// the owner of the manager that fired the ability.
	if (AWishCraftCharacter* WishCharacter = Cast<AWishCraftCharacter>(GetAvatarPawn()))
	{
		WishCharacter->SetInvisible(bInvisible);
	}
}
